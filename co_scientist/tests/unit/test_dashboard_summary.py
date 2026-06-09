"""Dashboard aggregation tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from co_scientist.models import (
    AssayProposal,
    Hypothesis,
    ResearchPlan,
    Session,
    Task,
    TherapeuticCandidate,
    Transcript,
)
from co_scientist.storage.repos import hypotheses as hyp_repo
from co_scientist.storage.repos import robin as robin_repo
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.storage.repos import tasks as task_repo
from co_scientist.storage.repos import transcripts as tx_repo
from co_scientist.web.dashboard import dashboard_to_dict, runs_index, session_dashboard
from co_scientist.workspace import ScientistWorkspace


def _now() -> datetime:
    return datetime.now(UTC)


async def _insert_session(
    conn,
    *,
    session_id: str,
    status: str = "running",
    workflow: str = "general_hypothesis",
    updated_delta_seconds: int = 0,
    final_overview: str | None = None,
) -> Session:
    now = _now()
    session = Session(
        id=session_id,
        created_at=now - timedelta(minutes=5),
        updated_at=now + timedelta(seconds=updated_delta_seconds),
        status=status,
        workflow=workflow,  # type: ignore[arg-type]
        research_goal=f"Goal for {session_id}",
        research_plan=ResearchPlan(
            objective=f"Objective for {session_id}",
            preferences=["prefer translational evidence"],
            constraints=["avoid unsafe protocols"],
            idea_attributes=["testable"],
            retrieval_queries=["query one", "query two"],
            clinical_or_translational=workflow == "therapeutic_discovery",
        ),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=5.0,
        budget_used_tokens=125,
        budget_used_usd=1.25,
        final_overview=final_overview,
    )
    await sess_repo.insert(conn, session)
    return session


async def test_session_dashboard_aggregates_run_health_and_progress(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_dash_running")
    now = _now()
    for index, status in enumerate(["pending", "in_progress", "done", "dead"]):
        await task_repo.enqueue(conn, Task(
            id=f"task_dash_{index}",
            session_id=session.id,
            created_at=now,
            started_at=now if status == "in_progress" else None,
            finished_at=now if status in {"done", "dead"} else None,
            agent="generation",
            action="CreateInitialHypotheses",
            payload={},
            status=status,  # type: ignore[arg-type]
            attempts=2 if status == "dead" else 0,
            last_error="example failure" if status == "dead" else None,
        ))
    await hyp_repo.insert(conn, Hypothesis(
        id="hyp_dash_1",
        session_id=session.id,
        created_at=now,
        created_by="generation",
        strategy="literature",
        title="Testable idea",
        summary="A short summary.",
        full_text="Full text.",
        artifact_path=f"artifacts/{session.id}/hypotheses/hyp_dash_1.json",
        state="in_tournament",
        elo=1210.0,
        matches_played=2,
    ))
    await tx_repo.insert(conn, Transcript(
        id="trn_dash_1",
        session_id=session.id,
        task_id=None,
        agent="generation",
        action="CreateInitialHypotheses",
        model="claude-opus-4-7",
        input_tokens=100,
        output_tokens=50,
        cache_read=25,
        cache_write=0,
        cost_usd=0.03,
        started_at=now,
        finished_at=now,
        artifact_path=f"artifacts/{session.id}/transcripts/trn_dash_1.json",
    ))

    dashboard = await session_dashboard(tmp_cfg, conn, session.id)

    assert dashboard.session.id == session.id
    assert dashboard.run_health.task_counts["pending"] == 1
    assert dashboard.run_health.task_counts["in_progress"] == 1
    assert dashboard.run_health.task_counts["done"] == 1
    assert dashboard.run_health.task_counts["dead"] == 1
    assert dashboard.run_health.attention_level == "danger"
    assert dashboard.budget_time.cost_used_usd == pytest.approx(1.25)
    assert dashboard.scientific_progress.hypotheses == 1
    assert dashboard.links.dashboard_path == f"/sessions/{session.id}/dashboard"
    assert any(panel.key == "generation" for panel in dashboard.phase_panels)


async def test_runs_index_pins_active_runs_and_uses_full_dashboard_links(tmp_cfg, conn) -> None:
    done = await _insert_session(
        conn,
        session_id="ses_dash_done",
        status="done",
        updated_delta_seconds=20,
        final_overview="artifacts/ses_dash_done/final/overview.md",
    )
    running = await _insert_session(
        conn,
        session_id="ses_dash_active",
        status="running",
        updated_delta_seconds=0,
    )
    paused = await _insert_session(
        conn,
        session_id="ses_dash_paused",
        status="paused",
        updated_delta_seconds=-10,
    )

    index = await runs_index(tmp_cfg, conn)

    assert [row.session_id for row in index.rows] == [running.id, paused.id, done.id]
    assert index.rows[0].dashboard_path == f"/sessions/{running.id}/dashboard"
    assert index.rows[2].overview_path == f"/sessions/{done.id}/overview"
    assert index.rows[0].short_id.endswith(running.id[-12:])


async def test_runs_index_uses_aggregate_queries_without_session_metrics(
    tmp_cfg, conn, monkeypatch
) -> None:
    session = await _insert_session(
        conn,
        session_id="ses_dash_index_robin",
        workflow="therapeutic_discovery",
    )
    assay = AssayProposal(
        id="assay_dash_index",
        session_id=session.id,
        created_at=_now(),
        strategy_name="RPE phagocytosis assay",
        reasoning="Functional disease model.",
        artifact_path=f"artifacts/{session.id}/robin/assay.json",
        rank_score=0.82,
        state="ranked",
    )
    candidate = TherapeuticCandidate(
        id="candidate_dash_index",
        session_id=session.id,
        assay_id=assay.id,
        created_at=_now(),
        candidate="Example kinase inhibitor",
        hypothesis="Improves RPE stress response.",
        reasoning="Mechanistic candidate.",
        artifact_path=f"artifacts/{session.id}/robin/candidate.json",
        rank_score=0.71,
        state="ranked",
    )
    await robin_repo.insert_assay(conn, assay)
    await robin_repo.insert_candidate(conn, candidate)

    async def _fail_metrics(*_args, **_kwargs):
        raise AssertionError("runs_index should not call per-session metrics")

    monkeypatch.setattr("co_scientist.web.dashboard.session_metrics_cached", _fail_metrics)

    index = await runs_index(tmp_cfg, conn)

    assert index.rows[0].session_id == session.id
    assert index.rows[0].scientific_summary == "1 assays, 1 candidates"


async def test_session_dashboard_marks_completed_sessions_as_static(tmp_cfg, conn) -> None:
    session = await _insert_session(
        conn,
        session_id="ses_dash_static",
        status="done",
        final_overview="artifacts/ses_dash_static/final/overview.md",
    )

    dashboard = await session_dashboard(tmp_cfg, conn, session.id)

    assert dashboard.refresh.enabled is False
    assert dashboard.links.overview_path == f"/sessions/{session.id}/overview"


async def test_session_dashboard_keeps_paused_sessions_live(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_dash_paused", status="paused")

    dashboard = await session_dashboard(tmp_cfg, conn, session.id)

    assert dashboard.refresh.enabled is True
    assert dashboard.refresh.sse_path == f"/api/sessions/{session.id}/events"


async def test_session_dashboard_does_not_create_missing_workspace_manifest(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_dash_no_workspace")
    workspace_root = tmp_cfg.data_dir / "workspaces" / session.id

    dashboard = await session_dashboard(tmp_cfg, conn, session.id)

    assert dashboard.evidence_artifacts == []
    assert not workspace_root.exists()


async def test_session_dashboard_skips_malformed_workspace_manifest_entries(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_dash_manifest_mixed")
    workspace = ScientistWorkspace(tmp_cfg, session.id)
    workspace.ensure()
    workspace.manifest_path.write_text(json.dumps([
        {"id": "missing_required_fields"},
        {
            "id": "art_valid_evidence",
            "kind": "project_file",
            "path": str(tmp_cfg.data_dir / "paper.pdf"),
            "title": "Valid evidence artifact",
        },
    ]))

    dashboard = await session_dashboard(tmp_cfg, conn, session.id)

    assert [artifact.id for artifact in dashboard.evidence_artifacts] == [
        "art_valid_evidence"
    ]


async def test_dashboard_to_dict_is_json_serializable(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_dash_json")

    payload = dashboard_to_dict(await session_dashboard(tmp_cfg, conn, session.id))

    assert payload["session"]["id"] == session.id
    assert payload["links"]["dashboard_path"] == f"/sessions/{session.id}/dashboard"
    assert payload["run_health"]["status"] == "running"
    assert payload["phase_panels"][0]["key"] == "prompt_plan"
    json.dumps(payload)


async def test_therapeutic_session_includes_assay_and_candidate_panels(tmp_cfg, conn) -> None:
    session = await _insert_session(
        conn,
        session_id="ses_dash_robin",
        workflow="therapeutic_discovery",
    )
    assay = AssayProposal(
        id="assay_dash",
        session_id=session.id,
        created_at=_now(),
        strategy_name="RPE phagocytosis assay",
        reasoning="Functional disease model.",
        artifact_path=f"artifacts/{session.id}/robin/assay.json",
        rank_score=0.82,
        state="ranked",
    )
    candidate = TherapeuticCandidate(
        id="candidate_dash",
        session_id=session.id,
        assay_id=assay.id,
        created_at=_now(),
        candidate="Example kinase inhibitor",
        hypothesis="Improves RPE stress response.",
        reasoning="Mechanistic candidate.",
        artifact_path=f"artifacts/{session.id}/robin/candidate.json",
        rank_score=0.71,
        state="ranked",
    )
    await robin_repo.insert_assay(conn, assay)
    await robin_repo.insert_candidate(conn, candidate)

    dashboard = await session_dashboard(tmp_cfg, conn, session.id)

    panel_keys = {panel.key for panel in dashboard.phase_panels}
    assert "assays" in panel_keys
    assert "candidates" in panel_keys
    assert dashboard.scientific_progress.assays == 1
    assert dashboard.scientific_progress.candidates == 1


async def test_phase_panel_task_counts_are_phase_specific(tmp_cfg, conn) -> None:
    session = await _insert_session(conn, session_id="ses_dash_phase_counts")
    now = _now()
    for index, (agent, action) in enumerate(
        [
            ("generation", "CreateInitialHypotheses"),
            ("generation", "GenerateFromFeedback"),
            ("reflection", "ReviewHypothesis"),
        ]
    ):
        await task_repo.enqueue(conn, Task(
            id=f"task_phase_{index}",
            session_id=session.id,
            created_at=now,
            finished_at=now,
            agent=agent,  # type: ignore[arg-type]
            action=action,  # type: ignore[arg-type]
            payload={},
            status="done",
        ))

    dashboard = await session_dashboard(tmp_cfg, conn, session.id)

    panels = {panel.key: panel for panel in dashboard.phase_panels}
    assert panels["generation"].counts["done_tasks"] == 2
    assert panels["review"].counts["done_tasks"] == 1
    assert panels["evolution"].counts["done_tasks"] == 0
