"""Supervisor scheduling for Robin-style therapeutic discovery sessions."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from co_scientist import ids
from co_scientist.agents.supervisor import Supervisor
from co_scientist.models import ResearchPlan, Session, Task, TaskResult
from co_scientist.storage.repos import sessions as sess_repo


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_session(conn) -> Session:
    session = Session(
        id="ses_robin_schedule",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        workflow="therapeutic_discovery",
        research_goal="Discover therapeutics for dry AMD",
        research_plan=ResearchPlan(objective="Discover therapeutics for dry AMD"),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    return session


@pytest.mark.asyncio
async def test_therapeutic_session_initializes_assay_generation(tmp_cfg, conn) -> None:
    session = await _make_session(conn)

    await Supervisor(tmp_cfg)._enqueue_initial_tasks(conn, session, n_initial=3)

    async with conn.execute(
        "SELECT agent, action, payload, idempotency_key FROM tasks WHERE session_id=?",
        (session.id,),
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "assay"
    assert row["action"] == "GenerateAssays"
    assert json.loads(row["payload"]) == {"round_index": 1, "num_assays": 10}
    assert row["idempotency_key"] == f"{session.id}::assay::generate::1"


@pytest.mark.asyncio
async def test_general_session_initializes_current_generation_flow(tmp_cfg, conn) -> None:
    session = Session(
        id="ses_general_schedule",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        workflow="general_hypothesis",
        research_goal="Explain mechanism X",
        research_plan=ResearchPlan(objective="Explain mechanism X"),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)

    await Supervisor(tmp_cfg)._enqueue_initial_tasks(conn, session, n_initial=2)

    async with conn.execute(
        "SELECT agent, action, payload FROM tasks WHERE session_id=? ORDER BY idempotency_key",
        (session.id,),
    ) as cur:
        rows = await cur.fetchall()

    assert [(r["agent"], r["action"], json.loads(r["payload"])) for r in rows] == [
        ("generation", "CreateInitialHypotheses", {"strategy": "literature", "n": 1}),
        ("generation", "CreateInitialHypotheses", {"strategy": "literature", "n": 1}),
    ]


@pytest.mark.asyncio
async def test_robin_followups_advance_assays_candidates_and_insights(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    supervisor = Supervisor(tmp_cfg)

    await supervisor._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="assay",
            action="GenerateAssays",
        ),
        TaskResult(kind="assay_created", assay_ids=["assay_a", "assay_b"]),
    )
    await supervisor._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="assay",
            action="EvaluateAssay",
        ),
        TaskResult(kind="assay_evaluated", assay_ids=["assay_a", "assay_b"]),
    )
    await supervisor._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="assay",
            action="RankAssays",
        ),
        TaskResult(kind="assays_ranked", assay_ids=["assay_a"], extra={"winner_assay_id": "assay_a"}),
    )
    await supervisor._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="candidate",
            action="GenerateCandidates",
        ),
        TaskResult(kind="candidate_created", candidate_ids=["cand_a", "cand_b"]),
    )
    await supervisor._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="candidate",
            action="EvaluateCandidate",
        ),
        TaskResult(kind="candidate_evaluated", candidate_ids=["cand_a", "cand_b"]),
    )
    await supervisor._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="analysis",
            action="AnalyzeExperimentalData",
        ),
        TaskResult(kind="analysis_completed", analysis_run_ids=["analysis_1"]),
    )
    await supervisor._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="result_interpreter",
            action="InterpretResults",
        ),
        TaskResult(kind="experiment_insight_created", insight_ids=["insight_1"]),
    )

    async with conn.execute(
        "SELECT agent, action, target_id, payload FROM tasks WHERE session_id=? ORDER BY created_at",
        (session.id,),
    ) as cur:
        rows = await cur.fetchall()

    actual = [(r["agent"], r["action"], r["target_id"], json.loads(r["payload"])) for r in rows]
    assert actual == [
        ("assay", "EvaluateAssay", "assay_a", {}),
        ("assay", "EvaluateAssay", "assay_b", {}),
        ("assay", "RankAssays", None, {"assay_ids": ["assay_a", "assay_b"]}),
        ("candidate", "GenerateCandidates", "assay_a", {"round_index": 1, "num_candidates": 30}),
        ("candidate", "EvaluateCandidate", "cand_a", {}),
        ("candidate", "EvaluateCandidate", "cand_b", {}),
        ("candidate", "RankCandidates", None, {"candidate_ids": ["cand_a", "cand_b"]}),
        ("result_interpreter", "InterpretResults", "analysis_1", {}),
        (
            "candidate",
            "RegenerateCandidatesFromResults",
            "insight_1",
            {"round_index": 2, "num_candidates": 10},
        ),
    ]
