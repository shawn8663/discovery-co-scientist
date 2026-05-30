"""Safety gates for goals and newly generated hypotheses."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from co_scientist.agents.base import AgentDeps
from co_scientist.agents.generation import GenerationAgent
from co_scientist.agents.supervisor import Supervisor
from co_scientist.models import ResearchPlan, Session
from co_scientist.safety.classifier import ClassifierResult, SafetyClassifier
from co_scientist.storage import db as db_mod
from co_scientist.storage.repos import hypotheses as hyp_repo
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.tools.registry import ToolRegistry


async def _make_session(conn, sid: str = "ses_safety") -> Session:
    session = Session(
        id=sid,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="running",
        research_goal="Test goal",
        research_plan=ResearchPlan(objective="Test goal"),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    return session


@pytest.mark.asyncio
async def test_supervisor_blocks_unsafe_goal_before_session_creation(tmp_cfg, monkeypatch) -> None:
    async def blocked_classify(self, text: str, *, label: str = "input") -> ClassifierResult:
        assert label == "research_goal"
        return ClassifierResult(categories=["cbrn"], confidence=0.99, rationale="unsafe")

    monkeypatch.setattr(SafetyClassifier, "classify", blocked_classify)

    with pytest.raises(RuntimeError):
        await Supervisor(tmp_cfg).run_session("Design a dangerous weaponized experiment")

    conn = await db_mod.connect(tmp_cfg)
    try:
        async with conn.execute("SELECT COUNT(*) AS n FROM sessions") as cur:
            row = await cur.fetchone()
        assert row["n"] == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_generation_quarantines_unsafe_hypothesis_and_suppresses_followup(
    tmp_cfg, conn, monkeypatch
) -> None:
    await _make_session(conn)

    async def blocked_classify(self, text: str, *, label: str = "input") -> ClassifierResult:
        assert label == "hypothesis"
        return ClassifierResult(categories=["dual_use_bio"], confidence=0.9, rationale="unsafe")

    monkeypatch.setattr(SafetyClassifier, "classify", blocked_classify)

    agent = GenerationAgent(
        AgentDeps(cfg=tmp_cfg, db=conn, llm=object(), tools=ToolRegistry(tmp_cfg))
    )
    hid, was_new = await agent._persist(
        "ses_safety",
        {
            "title": "Unsafe hypothesis",
            "statement": "Increase pathogen transmissibility for experimentation.",
            "mechanism": "A risky mechanism.",
        },
        strategy="literature",
    )

    assert was_new is False
    h = await hyp_repo.fetch(conn, hid)
    assert h is not None
    assert h.state == "quarantined"
    assert "Safety review" in h.full_text
