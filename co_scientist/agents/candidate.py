"""Robin candidate agent: propose, evaluate, rank, and regenerate candidates."""

from __future__ import annotations

from datetime import UTC, datetime

from .. import ids
from ..llm.anthropic_client import AgentCallSpec, CachedBlock, CallContext
from ..llm.prompts import render
from ..llm.routing import route
from ..models import Task, TaskResult, TherapeuticCandidate, TherapeuticCandidateEvaluation
from ..orchestrator.btl import rank_btl
from ..safety.gates import assess_safety
from ..storage.artifacts import write_json
from ..storage.repos import robin as robin_repo
from ..storage.repos import sessions as sess_repo
from .base import BaseAgent
from .robin_helpers import final_text, parse_candidate_blocks, split_evaluation_sections


class CandidateAgent(BaseAgent):
    name = "candidate"

    async def execute(self, task: Task) -> TaskResult:
        if task.action == "GenerateCandidates":
            return await self._generate(task, insight_id=None)
        if task.action == "RegenerateCandidatesFromResults":
            return await self._generate(task, insight_id=task.target_id)
        if task.action == "EvaluateCandidate":
            return await self._evaluate(task)
        if task.action == "RankCandidates":
            return await self._rank(task)
        raise ValueError(f"CandidateAgent does not handle action {task.action!r}")

    async def _generate(self, task: Task, *, insight_id: str | None) -> TaskResult:
        session = await sess_repo.fetch(self.deps.db, task.session_id)
        if session is None:
            raise RuntimeError(f"session {task.session_id} missing")
        assay_id = task.target_id if task.action == "GenerateCandidates" else None
        assay = await robin_repo.fetch_assay(self.deps.db, assay_id) if assay_id else None
        insight = await robin_repo.fetch_experiment_insight(self.deps.db, insight_id) if insight_id else None
        context_parts = []
        if assay:
            context_parts.append(f"Assay: {assay.strategy_name}\n{assay.reasoning}")
        if insight:
            context_parts.append(
                "Experiment insight:\n"
                + insight.model_dump_json(indent=2)
            )
        context = "\n\n".join(context_parts)
        num = int(task.payload.get("num_candidates", 30))
        prompt = render(
            "robin.candidate_generation",
            disease_name=session.research_plan.objective,
            num_candidates=num,
            therapeutic_candidate_review_output=context,
        )
        text = await self._call_text(task, prompt, mode="candidate_generation")
        records = parse_candidate_blocks(text)
        out: list[str] = []
        for record in records[:num]:
            cid = ids.artifact_id()
            safety = await assess_safety(
                self.deps.cfg,
                "\n\n".join(
                    [
                        record.get("candidate", ""),
                        record.get("hypothesis", ""),
                        record.get("reasoning", ""),
                    ]
                ),
                label="therapeutic_candidate",
            )
            artifact_path = await write_json(
                self.deps.cfg,
                session.id,
                "robin/candidates",
                cid,
                {
                    "record": record,
                    "prompt": "robin.candidate_generation",
                    "source_insight_id": insight_id,
                    "assay_id": assay.id if assay else None,
                    "safety": safety.artifact(),
                },
            )
            candidate = TherapeuticCandidate(
                id=cid,
                session_id=session.id,
                assay_id=assay.id if assay else None,
                created_at=datetime.now(UTC),
                round_index=int(task.payload.get("round_index", 1)),
                candidate=record.get("candidate", ""),
                hypothesis=record.get("hypothesis", ""),
                reasoning=record.get("reasoning", ""),
                artifact_path=artifact_path,
                state="quarantined" if safety.should_stop else "proposed",
            )
            if not safety.should_stop and await robin_repo.insert_candidate(self.deps.db, candidate):
                out.append(cid)
            elif safety.should_stop:
                await robin_repo.insert_candidate(self.deps.db, candidate)
        return TaskResult(kind="candidate_created", candidate_ids=out)

    async def _evaluate(self, task: Task) -> TaskResult:
        if not task.target_id:
            raise ValueError("EvaluateCandidate requires target_id")
        session = await sess_repo.fetch(self.deps.db, task.session_id)
        candidate = await robin_repo.fetch_candidate(self.deps.db, task.target_id)
        if session is None or candidate is None:
            raise RuntimeError("missing session or candidate")
        prompt = render(
            "robin.candidate_evaluation",
            disease_name=session.research_plan.objective,
            candidate=candidate.model_dump_json(indent=2),
        )
        text = await self._call_text(task, prompt, mode="candidate_evaluation")
        sections = split_evaluation_sections(
            text,
            (
                "Overview of Therapeutic Candidate",
                "Therapeutic History",
                "Mechanism of Action",
                "Expected Effect",
                "Overall Evaluation",
            ),
        )
        eid = ids.artifact_id()
        artifact_path = await write_json(
            self.deps.cfg,
            session.id,
            "robin/candidate_evaluations",
            eid,
            {"candidate_id": candidate.id, "text": text, "sections": sections},
        )
        evaluation = TherapeuticCandidateEvaluation(
            id=eid,
            candidate_id=candidate.id,
            session_id=session.id,
            created_at=datetime.now(UTC),
            overview=sections["Overview of Therapeutic Candidate"],
            therapeutic_history=sections["Therapeutic History"],
            mechanism_of_action=sections["Mechanism of Action"],
            expected_effect=sections["Expected Effect"],
            overall_evaluation=sections["Overall Evaluation"],
            artifact_path=artifact_path,
        )
        await robin_repo.insert_candidate_evaluation(self.deps.db, evaluation)
        return TaskResult(kind="candidate_evaluated", candidate_ids=[candidate.id])

    async def _rank(self, task: Task) -> TaskResult:
        candidate_ids = list(task.payload.get("candidate_ids") or [])
        if not candidate_ids:
            candidates = await robin_repo.list_candidates(self.deps.db, task.session_id)
            candidate_ids = [c.id for c in candidates]
        comparisons = [
            tuple(item) for item in task.payload.get("comparisons", [])
            if isinstance(item, (list, tuple)) and len(item) == 3
        ]
        scores = rank_btl(candidate_ids, comparisons) if comparisons else {
            cid: float(len(candidate_ids) - i) for i, cid in enumerate(candidate_ids)
        }
        for cid, score in scores.items():
            await robin_repo.set_candidate_rank_score(self.deps.db, cid, float(score))
        winner = next(iter(scores), None)
        return TaskResult(
            kind="candidates_ranked",
            candidate_ids=list(scores.keys()),
            extra={"winner_candidate_id": winner},
        )

    async def _call_text(self, task: Task, prompt: str, *, mode: str) -> str:
        spec = AgentCallSpec(
            route=route(self.deps.cfg, "generation", "literature"),
            system_blocks=[CachedBlock(self._system_prompt_header(), cache=True)],
            user_blocks=[CachedBlock(prompt, cache=False)],
            tools=[],
            max_output_tokens=4096,
        )
        resp = await self.deps.llm.call(
            spec,
            CallContext(
                session_id=task.session_id,
                task_id=task.id,
                agent=self.name,
                action=task.action,
                mode=mode,
            ),
        )
        return final_text(resp)
