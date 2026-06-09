"""Web route tests for dashboard pages."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from co_scientist.models import ResearchPlan, Session
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.web.app import create_app


def _now() -> datetime:
    return datetime.now(UTC)


async def _insert_session(
    conn,
    *,
    session_id: str,
    status: str = "running",
    workflow: str = "general_hypothesis",
    final_overview: str | None = None,
) -> Session:
    session = Session(
        id=session_id,
        created_at=_now(),
        updated_at=_now(),
        status=status,
        workflow=workflow,  # type: ignore[arg-type]
        research_goal=f"Dashboard goal for {session_id}",
        research_plan=ResearchPlan(objective=f"Dashboard objective for {session_id}"),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=5.0,
        budget_used_tokens=100,
        budget_used_usd=0.5,
        final_overview=final_overview,
    )
    await sess_repo.insert(conn, session)
    return session


async def test_runs_index_lists_active_and_past_runs(tmp_cfg, conn) -> None:
    active = await _insert_session(conn, session_id="ses_web_runs_active", status="running")
    done = await _insert_session(
        conn,
        session_id="ses_web_runs_done",
        status="done",
        final_overview="artifacts/ses_web_runs_done/final/overview.md",
    )

    response = TestClient(create_app(tmp_cfg)).get("/runs")

    assert response.status_code == 200
    assert "Runs" in response.text
    assert active.id[-12:] in response.text
    assert done.id[-12:] in response.text
    assert f"/sessions/{active.id}/dashboard" in response.text
    assert f"/sessions/{done.id}/overview" in response.text
    assert "Dashboard goal for ses_web_runs_active" in response.text


async def test_root_renders_runs_index(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_web_root_index", status="running")

    response = TestClient(create_app(tmp_cfg)).get("/")

    assert response.status_code == 200
    assert "Runs" in response.text
    assert f"/sessions/{session.id}/dashboard" in response.text
