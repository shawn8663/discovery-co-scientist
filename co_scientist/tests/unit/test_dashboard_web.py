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
    assert "general_hypothesis" in response.text
    assert "0 active, 0 pending, 0 failed" in response.text
    assert "0 hypotheses, 0 matches" in response.text
    assert "$0.50 / $5.00" in response.text


async def test_root_renders_runs_index(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_web_root_index", status="running")

    response = TestClient(create_app(tmp_cfg)).get("/")

    assert response.status_code == 200
    assert "Runs" in response.text
    assert f"/sessions/{session.id}/dashboard" in response.text


async def test_session_dashboard_renders_command_center(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_web_dashboard", status="running")

    response = TestClient(create_app(tmp_cfg)).get(f"/sessions/{session.id}/dashboard")

    assert response.status_code == 200
    assert "Run health" in response.text
    assert "Budget and time" in response.text
    assert "Scientific progress" in response.text
    assert "Prompt and plan" in response.text
    assert "Evidence" in response.text
    assert "Generation" in response.text
    assert f"/api/sessions/{session.id}/dashboard-summary" in response.text
    assert f"/api/sessions/{session.id}/events" in response.text


async def test_dashboard_pages_expose_stable_css_hooks(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_web_dashboard_css", status="running")

    runs_response = TestClient(create_app(tmp_cfg)).get("/runs")
    dashboard_response = TestClient(create_app(tmp_cfg)).get(
        f"/sessions/{session.id}/dashboard"
    )

    assert 'class="runs-index"' in runs_response.text
    assert 'class="dashboard-shell"' in dashboard_response.text
    assert 'class="dashboard-top"' in dashboard_response.text
    assert 'class="phase-grid"' in dashboard_response.text
    assert 'class="events-log"' in dashboard_response.text


async def test_completed_session_dashboard_does_not_enable_polling(tmp_cfg, conn) -> None:
    session = await _insert_session(
        conn,
        session_id="ses_web_dashboard_done",
        status="done",
        final_overview="artifacts/ses_web_dashboard_done/final/overview.md",
    )

    response = TestClient(create_app(tmp_cfg)).get(f"/sessions/{session.id}/dashboard")

    assert response.status_code == 200
    assert "Final overview ready" in response.text
    assert 'data-refresh-enabled="false"' in response.text
    assert "new EventSource(" not in response.text


async def test_therapeutic_session_dashboard_uses_robin_panels(tmp_cfg, conn) -> None:
    session = await _insert_session(
        conn,
        session_id="ses_web_dashboard_robin",
        status="running",
        workflow="therapeutic_discovery",
    )

    response = TestClient(create_app(tmp_cfg)).get(f"/sessions/{session.id}/dashboard")

    assert response.status_code == 200
    assert "Assays" in response.text
    assert "Candidates" in response.text
    assert "Analysis" in response.text


async def test_dashboard_placeholder_returns_404_for_missing_session(tmp_cfg) -> None:
    response = TestClient(create_app(tmp_cfg)).get("/sessions/ses_missing_dashboard/dashboard")

    assert response.status_code == 404


async def test_dashboard_summary_endpoint_returns_structured_json(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_web_dashboard_json", status="running")

    response = TestClient(create_app(tmp_cfg)).get(
        f"/api/sessions/{session.id}/dashboard-summary"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["id"] == session.id
    assert payload["run_health"]["status"] == "running"
    assert payload["links"]["dashboard_path"] == f"/sessions/{session.id}/dashboard"
    assert isinstance(payload["phase_panels"], list)


async def test_dashboard_summary_endpoint_returns_404_for_missing_session(tmp_cfg) -> None:
    response = TestClient(create_app(tmp_cfg)).get(
        "/api/sessions/ses_missing_dashboard/dashboard-summary"
    )

    assert response.status_code == 404
