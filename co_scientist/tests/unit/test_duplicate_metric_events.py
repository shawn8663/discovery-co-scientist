"""Duplicate suppression paths should emit metrics breadcrumbs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from co_scientist import ids
from co_scientist.agents.base import AgentDeps
from co_scientist.agents.generation import GenerationAgent
from co_scientist.agents.reflection import ReflectionAgent
from co_scientist.models import Hypothesis, ResearchPlan, Session, Task
from co_scientist.storage.repos import events as events_repo
from co_scientist.storage.repos import hypotheses as hyp_repo
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.tools.registry import ToolRegistry


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_session(conn, sid: str = "ses_duplicate_events") -> Session:
    session = Session(
        id=sid,
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


@pytest.mark.asyncio
async def test_generation_deterministic_duplicate_emits_event(tmp_cfg, conn) -> None:
    session = await _make_session(conn, "ses_generation_det_dup")
    agent = GenerationAgent(
        AgentDeps(cfg=tmp_cfg, db=conn, llm=object(), tools=ToolRegistry(tmp_cfg))
    )
    record = {
        "title": "Duplicate assay",
        "statement": "The same assay hypothesis.",
        "mechanism": "The same mechanism.",
    }

    _, was_new_first = await agent._persist(session.id, record, strategy="literature")
    duplicate_id, was_new_second = await agent._persist(session.id, record, strategy="literature")

    assert was_new_first is True
    assert was_new_second is False
    recent = await events_repo.recent(conn, session.id, limit=10)
    event = next(e for e in recent if e["event"] == "hypothesis_duplicate_suppressed")
    assert event["agent"] == "generation"
    assert event["payload"]["reason"] == "deterministic"
    assert event["payload"]["proposed_hypothesis_id"] == duplicate_id
    assert event["payload"]["existing_hypothesis_id"] == duplicate_id


@pytest.mark.asyncio
async def test_generation_semantic_duplicate_emits_event(tmp_cfg, conn, monkeypatch) -> None:
    session = await _make_session(conn, "ses_generation_sem_dup")
    existing_id = ids.hypothesis_id(session.id, "generation/literature", "existing")
    await hyp_repo.insert(conn, Hypothesis(
        id=existing_id,
        session_id=session.id,
        created_at=_now(),
        created_by="generation",
        strategy="literature",
        title="Existing",
        summary="Existing summary",
        full_text="Existing full text",
        artifact_path=f"artifacts/{session.id}/hypotheses/{existing_id}.json",
        state="draft",
    ))

    async def semantic_duplicate(self, session_id: str, text: str):
        return existing_id, None

    monkeypatch.setattr(GenerationAgent, "_dedup_query", semantic_duplicate)
    agent = GenerationAgent(
        AgentDeps(cfg=tmp_cfg, db=conn, llm=object(), tools=ToolRegistry(tmp_cfg))
    )
    duplicate_id, was_new = await agent._persist(
        session.id,
        {
            "title": "Near duplicate assay",
            "statement": "A near duplicate assay hypothesis.",
            "mechanism": "A near duplicate mechanism.",
        },
        strategy="literature",
    )

    assert duplicate_id == existing_id
    assert was_new is False
    recent = await events_repo.recent(conn, session.id, limit=10)
    event = next(e for e in recent if e["event"] == "hypothesis_duplicate_suppressed")
    assert event["agent"] == "generation"
    assert event["payload"]["reason"] == "semantic"
    assert event["payload"]["existing_hypothesis_id"] == existing_id


@pytest.mark.asyncio
async def test_reflection_clustered_duplicate_emits_event(tmp_cfg, conn) -> None:
    session = await _make_session(conn, "ses_reflection_cluster_event")
    canonical_id = ids.hypothesis_id(session.id, "generation/literature", "canonical")
    duplicate_id = ids.hypothesis_id(session.id, "generation/literature", "duplicate")
    for hid, state in ((canonical_id, "reviewed"), (duplicate_id, "draft")):
        await hyp_repo.insert(conn, Hypothesis(
            id=hid,
            session_id=session.id,
            created_at=_now(),
            created_by="generation",
            strategy="literature",
            title=hid,
            summary="Cluster summary",
            full_text="Cluster full text",
            artifact_path=f"artifacts/{session.id}/hypotheses/{hid}.json",
            state=state,  # type: ignore[arg-type]
            dedup_cluster="c0001",
        ))

    agent = ReflectionAgent(
        AgentDeps(cfg=tmp_cfg, db=conn, llm=object(), tools=ToolRegistry(tmp_cfg))
    )
    result = await agent.execute(Task(
        id=ids.task_id(),
        session_id=session.id,
        created_at=_now(),
        agent="reflection",
        action="ReviewHypothesis",
        target_id=duplicate_id,
        payload={"kind": "full"},
    ))

    assert result.kind == "noop"
    recent = await events_repo.recent(conn, session.id, limit=10)
    event = next(e for e in recent if e["event"] == "hypothesis_duplicate_suppressed")
    assert event["agent"] == "reflection"
    assert event["payload"]["reason"] == "clustered"
    assert event["payload"]["proposed_hypothesis_id"] == duplicate_id
    assert event["payload"]["existing_hypothesis_id"] == canonical_id
    assert event["payload"]["dedup_cluster"] == "c0001"
