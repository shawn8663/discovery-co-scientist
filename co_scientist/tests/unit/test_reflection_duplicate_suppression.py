"""Reflection should skip near-duplicate drafts before expensive review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from co_scientist import ids
from co_scientist.agents.base import AgentDeps
from co_scientist.agents.reflection import ReflectionAgent
from co_scientist.models import Hypothesis, ResearchPlan, Session, Task
from co_scientist.storage.repos import hypotheses as hyp_repo
from co_scientist.storage.repos import reviews as rev_repo
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.tools.registry import ToolRegistry


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_session(conn) -> Session:
    session = Session(
        id="ses_reflection_dedup",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        research_goal="Find a better assay design.",
        research_plan=ResearchPlan(objective="Find a better assay design."),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    return session


async def _insert_hypothesis(
    conn,
    session_id: str,
    *,
    hid: str,
    created_at: datetime,
    state: str = "draft",
    cluster: str = "c0001",
) -> None:
    await hyp_repo.insert(
        conn,
        Hypothesis(
            id=hid,
            session_id=session_id,
            created_at=created_at,
            created_by="generation",
            strategy="literature",
            title=f"title {hid[-4:]}",
            summary="A similar assay hypothesis.",
            full_text="Full hypothesis text.",
            artifact_path=f"artifacts/{session_id}/hypotheses/{hid}.json",
            state=state,  # type: ignore[arg-type]
            dedup_cluster=cluster,
        ),
    )


@pytest.mark.asyncio
async def test_reflection_retires_later_duplicate_draft_before_llm(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    canonical_id = ids.hypothesis_id(session.id, "generation/literature", "canonical")
    duplicate_id = ids.hypothesis_id(session.id, "generation/literature", "duplicate")
    started = _now()
    await _insert_hypothesis(
        conn,
        session.id,
        hid=canonical_id,
        created_at=started,
        state="reviewed",
    )
    await _insert_hypothesis(
        conn,
        session.id,
        hid=duplicate_id,
        created_at=started + timedelta(seconds=1),
    )

    agent = ReflectionAgent(
        AgentDeps(cfg=tmp_cfg, db=conn, llm=object(), tools=ToolRegistry(tmp_cfg))
    )
    result = await agent.execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="reflection",
            action="ReviewHypothesis",
            target_id=duplicate_id,
            payload={"kind": "full"},
        )
    )

    assert result.kind == "noop"
    assert result.hypothesis_ids == [duplicate_id]
    assert result.extra == {
        "reason": "duplicate_cluster_suppressed",
        "canonical_hypothesis_id": canonical_id,
        "dedup_cluster": "c0001",
    }
    duplicate = await hyp_repo.fetch(conn, duplicate_id)
    assert duplicate is not None
    assert duplicate.state == "retired"
    assert await rev_repo.list_for_hypothesis(conn, duplicate_id) == []
