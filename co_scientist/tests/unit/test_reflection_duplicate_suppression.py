"""Reflection should skip near-duplicate drafts before expensive review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from co_scientist import ids
from co_scientist.agents.base import AgentDeps
from co_scientist.agents.reflection import ReflectionAgent
from co_scientist.agents.schemas import RECORD_REVIEW_TOOL
from co_scientist.llm.anthropic_client import AnthropicResponse
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


@pytest.mark.asyncio
async def test_screen_reflection_uses_no_retrieval_tools_and_rejects_low_promise(
    tmp_cfg, conn
) -> None:
    session = await _make_session(conn)
    hypothesis_id = ids.hypothesis_id(session.id, "generation/literature", "low promise")
    await _insert_hypothesis(
        conn,
        session.id,
        hid=hypothesis_id,
        created_at=_now(),
        cluster="screen-low",
    )
    raw = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="record_review",
                input={
                    "kind": "screen",
                    "verdict": "already_explained",
                    "novelty": 0.1,
                    "correctness": 0.8,
                    "testability": 0.4,
                    "feasibility": 0.8,
                    "evidence": [],
                    "notes": "Known mechanism; skip expensive review.",
                },
            )
        ],
    )
    response = AnthropicResponse(
        raw=raw,
        transcript_id="trn_screen_low",
        cost_usd=0.001,
        input_tokens=200,
        output_tokens=80,
        cache_read=0,
        cache_write=0,
    )
    deps = AgentDeps(
        cfg=tmp_cfg,
        db=conn,
        llm=MagicMock(call=AsyncMock(return_value=response)),
        tools=MagicMock(),
    )
    deps.tools.anthropic_tools_for.side_effect = AssertionError(
        "screen reflection must not request retrieval tools"
    )

    result = await ReflectionAgent(deps).execute(
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="reflection",
            action="ReviewHypothesis",
            target_id=hypothesis_id,
            payload={"kind": "screen"},
        )
    )

    assert result.kind == "review_completed"
    assert result.extra == {
        "kind": "screen",
        "verdict": "already_explained",
        "promising": False,
    }
    spec = deps.llm.call.await_args.args[0]
    assert spec.tools == [RECORD_REVIEW_TOOL]
    assert spec.tool_choice == {"type": "tool", "name": "record_review"}
    hypothesis = await hyp_repo.fetch(conn, hypothesis_id)
    assert hypothesis is not None
    assert hypothesis.state == "rejected"
    reviews = await rev_repo.list_for_hypothesis(conn, hypothesis_id)
    assert [r.kind for r in reviews] == ["screen"]
