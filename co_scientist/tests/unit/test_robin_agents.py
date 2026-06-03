"""Robin agent execution without live LLM calls."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from co_scientist import ids
from co_scientist.agents.analysis import AnalysisAgent
from co_scientist.agents.assay import AssayAgent
from co_scientist.agents.base import AgentDeps
from co_scientist.agents.candidate import CandidateAgent
from co_scientist.agents.result_interpreter import ResultInterpreterAgent
from co_scientist.models import AnalysisRun, AssayProposal, ResearchPlan, Session, Task
from co_scientist.storage.repos import robin as robin_repo
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.tools.registry import ToolRegistry
from co_scientist.workspace import ScientistWorkspace


class _FakeLLM:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = []

    async def call(self, spec, ctx, *, est_input_tokens=None):
        self.calls.append((spec, ctx, est_input_tokens))
        text = self.outputs.pop(0)
        raw = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
        )
        return SimpleNamespace(
            raw=raw,
            transcript_id=ids.transcript_id(),
            cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
            cache_read=0,
            cache_write=0,
        )


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_session(conn) -> Session:
    s = Session(
        id="ses_robin_agents",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        workflow="therapeutic_discovery",
        research_goal="Discover therapeutics for dry AMD",
        research_plan=ResearchPlan(objective="Discover therapeutics for dry AMD"),
        config_snapshot={},
        budget_tokens=10000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, s)
    return s


def _deps(tmp_cfg, conn, outputs: list[str]) -> AgentDeps:
    return AgentDeps(
        cfg=tmp_cfg,
        db=conn,
        llm=_FakeLLM(outputs),
        tools=ToolRegistry(tmp_cfg).discover(),
    )


@pytest.mark.asyncio
async def test_assay_agent_generates_evaluates_and_ranks_assays(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    deps = _deps(
        tmp_cfg,
        conn,
        [
            """[
              {"strategy_name":"RPE phagocytosis assay","reasoning":"Functional endpoint"},
              {"strategy_name":"Oxidative stress assay","reasoning":"Disease stress endpoint"}
            ]""",
            "Assay Overview: overview\nBiomedical Evidence: evidence\nPrevious Use: prior\nOverall Evaluation: strong",
        ],
    )
    agent = AssayAgent(deps)

    created = await agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="assay",
            action="GenerateAssays",
            payload={"round_index": 1, "num_assays": 2},
        )
    )
    assert created.kind == "assay_created"
    assert len(created.assay_ids) == 2

    evaluated = await agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="assay",
            action="EvaluateAssay",
            target_id=created.assay_ids[0],
        )
    )
    assert evaluated.kind == "assay_evaluated"
    assert evaluated.assay_ids == [created.assay_ids[0]]

    ranked = await agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="assay",
            action="RankAssays",
            payload={"assay_ids": created.assay_ids},
        )
    )
    assert ranked.kind == "assays_ranked"
    assert ranked.extra["winner_assay_id"] == created.assay_ids[0]


@pytest.mark.asyncio
async def test_candidate_agent_generates_evaluates_and_regenerates_from_insight(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    assay = AssayProposal(
        id="assay_a",
        session_id=session.id,
        created_at=_now(),
        strategy_name="RPE phagocytosis assay",
        reasoning="Functional endpoint",
        artifact_path=f"artifacts/{session.id}/robin/assay_a.json",
    )
    await robin_repo.insert_assay(conn, assay)
    analysis = AnalysisRun(
        id="analysis_a",
        session_id=session.id,
        created_at=_now(),
        kind="flow_cytometry",
        summary="Ripasudil increased MFI.",
        artifact_path=f"artifacts/{session.id}/analysis/analysis_a.json",
    )
    await robin_repo.insert_analysis_run(conn, analysis)
    await robin_repo.insert_experiment_insight(
        conn,
        __import__("co_scientist.models", fromlist=["ExperimentInsight"]).ExperimentInsight(
            id="insight_a",
            session_id=session.id,
            analysis_run_id=analysis.id,
            created_at=_now(),
            summary="ROCK inhibition is a hit class.",
            positive_hits=["Ripasudil"],
            constraints=["Avoid cytotoxic concentrations"],
            artifact_path=f"artifacts/{session.id}/insights/insight_a.json",
        ),
    )
    outputs = [
        """<CANDIDATE START>
CANDIDATE: Ripasudil
HYPOTHESIS: ROCK inhibition enhances RPE phagocytosis.
REASONING: Strong target validation and ocular feasibility.
<CANDIDATE END>""",
        "Overview of Therapeutic Candidate: overview\nTherapeutic History: history\nMechanism of Action: moa\nExpected Effect: effect\nOverall Evaluation: good",
        """<CANDIDATE START>
CANDIDATE: KL001
HYPOTHESIS: CRY modulation enhances RPE phagocytosis.
REASONING: Experiment-informed regeneration.
<CANDIDATE END>""",
    ]
    agent = CandidateAgent(_deps(tmp_cfg, conn, outputs))

    created = await agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="candidate",
            action="GenerateCandidates",
            target_id=assay.id,
            payload={"round_index": 1, "num_candidates": 1},
        )
    )
    assert created.kind == "candidate_created"
    assert len(created.candidate_ids) == 1

    evaluated = await agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="candidate",
            action="EvaluateCandidate",
            target_id=created.candidate_ids[0],
        )
    )
    assert evaluated.kind == "candidate_evaluated"

    regenerated = await agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="candidate",
            action="RegenerateCandidatesFromResults",
            target_id="insight_a",
            payload={"round_index": 2, "num_candidates": 1},
        )
    )
    assert regenerated.kind == "candidate_created"
    assert len(regenerated.candidate_ids) == 1


@pytest.mark.asyncio
async def test_analysis_and_result_interpreter_create_workspace_artifacts(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    dataset = ScientistWorkspace(tmp_cfg, session.id).add_artifact(
        kind="dataset",
        path="datasets/mock_counts.csv",
        title="Mock counts",
    )
    analysis_agent = AnalysisAgent(_deps(tmp_cfg, conn, []))
    result = await analysis_agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="analysis",
            action="AnalyzeExperimentalData",
            payload={"kind": "rnaseq", "dataset_artifact_ids": [dataset.id], "trajectories": 3},
        )
    )
    assert result.kind == "analysis_completed"
    assert len(result.analysis_run_ids) == 1

    interpreter = ResultInterpreterAgent(
        _deps(
            tmp_cfg,
            conn,
            [
                """{
                  "summary":"ABCA1 signal supports lipid efflux follow-up.",
                  "positive_hits":["ABCA1"],
                  "negative_hits":[],
                  "suggested_mechanisms":["lipid efflux"],
                  "follow_up_assays":["Dose-response validation"],
                  "constraints":["Use outlines, not precise protocols"]
                }"""
            ],
        )
    )
    interpreted = await interpreter.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="result_interpreter",
            action="InterpretResults",
            target_id=result.analysis_run_ids[0],
        )
    )
    assert interpreted.kind == "experiment_insight_created"
    assert len(interpreted.insight_ids) == 1
