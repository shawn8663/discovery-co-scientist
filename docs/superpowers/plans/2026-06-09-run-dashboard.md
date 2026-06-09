# Run Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a canonical runs index and per-session dashboard for monitoring active Discovery Co-Scientist runs and reviewing past runs.

**Architecture:** Add a read-only dashboard aggregation module that derives view models from existing SQLite tables and workspace artifacts. Render those view models through FastAPI/Jinja routes, with htmx/SSE refresh affordances for running sessions and stable links for historical review.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, htmx, SSE, aiosqlite, Pico.css, pytest, FastAPI TestClient.

---

## File Structure

- Create `co_scientist/web/dashboard.py`
  - Owns dashboard aggregation dataclasses and read-only async query helpers.
  - Provides `runs_index()` and `session_dashboard()` functions for route handlers.
  - Keeps SQL aggregation out of templates and limits growth in `web/app.py`.

- Modify `co_scientist/web/app.py`
  - Add `/runs`.
  - Make `/` render the runs index.
  - Add `/sessions/{session_id}/dashboard`.
  - Add `/api/sessions/{session_id}/dashboard-summary`.
  - Keep existing `/sessions/{session_id}` detail page intact.

- Create `co_scientist/web/templates/runs.html`
  - New canonical run review hub.

- Create `co_scientist/web/templates/session_dashboard.html`
  - Per-session command center.

- Modify `co_scientist/web/templates/base.html`
  - Add navigation link to Runs.
  - Keep New session action.

- Modify `co_scientist/web/static/app.css`
  - Add dashboard grid, health panel, phase panel, metric, and responsive styles.

- Modify `co_scientist/cli.py`
  - Add URL helper for dashboard links.
  - Print global runs link before/after commands that create or resume runs.
  - Print per-session dashboard link when a session ID is available.

- Create `co_scientist/tests/unit/test_dashboard_summary.py`
  - Unit coverage for aggregation and view model behavior.

- Create `co_scientist/tests/unit/test_dashboard_web.py`
  - Route/template coverage for `/runs` and per-session dashboards.

- Modify or add `co_scientist/tests/unit/test_cli_dashboard_links.py`
  - Focused coverage for dashboard URL helper and CLI output behavior that can be tested without launching LLM calls.

---

## Task 1: Dashboard Aggregation View Models

**Files:**
- Create: `co_scientist/web/dashboard.py`
- Test: `co_scientist/tests/unit/test_dashboard_summary.py`

- [ ] **Step 1: Write failing aggregation tests**

Create `co_scientist/tests/unit/test_dashboard_summary.py` with these tests:

```python
"""Dashboard aggregation tests."""

from __future__ import annotations

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
from co_scientist.web.dashboard import runs_index, session_dashboard


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

    index = await runs_index(tmp_cfg, conn)

    assert [row.session_id for row in index.rows] == [running.id, done.id]
    assert index.rows[0].dashboard_path == f"/sessions/{running.id}/dashboard"
    assert index.rows[1].overview_path == f"/sessions/{done.id}/overview"
    assert index.rows[0].short_id.endswith(running.id[-12:])


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_summary.py -q
```

Expected: FAIL because `co_scientist.web.dashboard` does not exist.

- [ ] **Step 3: Implement dashboard aggregation dataclasses and helpers**

Create `co_scientist/web/dashboard.py`:

```python
"""Read-only dashboard aggregation for the web UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from ..config import Config
from ..models import Session
from ..obs.metrics import session_metrics_cached, to_dict
from ..storage.repos import events as events_repo
from ..storage.repos import robin as robin_repo
from ..storage.repos import sessions as sess_repo
from ..workspace import ScientistWorkspace

ACTIVE_STATUSES = {"running", "paused"}


@dataclass
class DashboardLinks:
    dashboard_path: str
    overview_path: str | None = None
    legacy_detail_path: str | None = None


@dataclass
class RunHealth:
    status: str
    task_counts: dict[str, int]
    active_task_label: str
    latest_event_label: str
    attention_level: str
    dead_tasks: int = 0
    retry_tasks: int = 0


@dataclass
class BudgetTime:
    cost_used_usd: float
    cost_budget_usd: float
    tokens_used: int
    token_budget: int
    llm_calls: int
    p95_latency_ms: float | None
    wall_deadline: datetime | None


@dataclass
class ScientificProgress:
    evidence_sources: int = 0
    hypotheses: int = 0
    reviews: int = 0
    matches: int = 0
    assays: int = 0
    candidates: int = 0
    analysis_runs: int = 0
    experiment_insights: int = 0
    final_overview_ready: bool = False


@dataclass
class PhasePanel:
    key: str
    title: str
    status: str
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    href: str | None = None


@dataclass
class RefreshPolicy:
    enabled: bool
    health_interval_ms: int = 2000
    summary_interval_ms: int = 8000


@dataclass
class SessionDashboard:
    session: Session
    short_id: str
    run_health: RunHealth
    budget_time: BudgetTime
    scientific_progress: ScientificProgress
    phase_panels: list[PhasePanel]
    recent_events: list[dict[str, Any]]
    links: DashboardLinks
    refresh: RefreshPolicy
    metrics: dict[str, Any]


@dataclass
class RunIndexRow:
    session_id: str
    short_id: str
    status: str
    workflow: str
    research_goal: str
    updated_at: str
    budget_used_usd: float
    budget_usd: float
    health_label: str
    progress_label: str
    dashboard_path: str
    overview_path: str | None


@dataclass
class RunIndex:
    rows: list[RunIndexRow]


async def runs_index(cfg: Config, conn: aiosqlite.Connection) -> RunIndex:
    del cfg
    async with conn.execute(
        """SELECT id, status, workflow, research_goal, updated_at,
                  budget_used_usd, budget_usd, final_overview,
                  (SELECT COUNT(*) FROM tasks WHERE session_id=s.id AND status IN ('leased','in_progress')) AS active_tasks,
                  (SELECT COUNT(*) FROM tasks WHERE session_id=s.id AND status='pending') AS pending_tasks,
                  (SELECT COUNT(*) FROM tasks WHERE session_id=s.id AND status='dead') AS dead_tasks,
                  (SELECT COUNT(*) FROM hypotheses WHERE session_id=s.id) AS n_hypotheses,
                  (SELECT COUNT(*) FROM reviews WHERE session_id=s.id) AS n_reviews,
                  (SELECT COUNT(*) FROM tournament_matches WHERE session_id=s.id) AS n_matches,
                  (SELECT COUNT(*) FROM assay_proposals WHERE session_id=s.id) AS n_assays,
                  (SELECT COUNT(*) FROM therapeutic_candidates WHERE session_id=s.id) AS n_candidates
             FROM sessions s
             ORDER BY CASE WHEN status IN ('running','paused') THEN 0 ELSE 1 END,
                      updated_at DESC"""
    ) as cur:
        rows = await cur.fetchall()
    return RunIndex(rows=[_run_index_row(row) for row in rows])


async def session_dashboard(
    cfg: Config,
    conn: aiosqlite.Connection,
    session_id: str,
) -> SessionDashboard:
    session = await sess_repo.fetch(conn, session_id)
    if session is None:
        raise KeyError(session_id)
    metrics_obj = await session_metrics_cached(conn, session_id)
    metrics = to_dict(metrics_obj)
    task_counts = await _task_counts(conn, session_id)
    active_task_label = await _active_task_label(conn, session_id)
    recent_events = await events_repo.recent(conn, session_id, limit=12)
    progress = await _scientific_progress(cfg, conn, session)
    links = _links(session)
    run_health = RunHealth(
        status=session.status,
        task_counts=task_counts,
        active_task_label=active_task_label,
        latest_event_label=_latest_event_label(recent_events),
        attention_level=_attention_level(session.status, task_counts),
        dead_tasks=task_counts.get("dead", 0),
        retry_tasks=await _retry_count(conn, session_id),
    )
    budget_time = BudgetTime(
        cost_used_usd=session.budget_used_usd,
        cost_budget_usd=session.budget_usd,
        tokens_used=session.budget_used_tokens,
        token_budget=session.budget_tokens,
        llm_calls=int(metrics["n_calls"]),
        p95_latency_ms=metrics["p95_latency_ms"],
        wall_deadline=session.wall_deadline,
    )
    return SessionDashboard(
        session=session,
        short_id=_short_id(session.id),
        run_health=run_health,
        budget_time=budget_time,
        scientific_progress=progress,
        phase_panels=await _phase_panels(cfg, conn, session, progress, metrics),
        recent_events=recent_events,
        links=links,
        refresh=RefreshPolicy(enabled=session.status == "running"),
        metrics=metrics,
    )


def dashboard_to_dict(dashboard: SessionDashboard) -> dict[str, Any]:
    return {
        "session": dashboard.session.model_dump(mode="json"),
        "short_id": dashboard.short_id,
        "run_health": dashboard.run_health.__dict__,
        "budget_time": dashboard.budget_time.__dict__,
        "scientific_progress": dashboard.scientific_progress.__dict__,
        "phase_panels": [panel.__dict__ for panel in dashboard.phase_panels],
        "recent_events": dashboard.recent_events,
        "links": dashboard.links.__dict__,
        "refresh": dashboard.refresh.__dict__,
        "metrics": dashboard.metrics,
    }


def _run_index_row(row: aiosqlite.Row) -> RunIndexRow:
    session_id = row["id"]
    n_hypotheses = row["n_hypotheses"] or 0
    n_reviews = row["n_reviews"] or 0
    n_matches = row["n_matches"] or 0
    n_assays = row["n_assays"] or 0
    n_candidates = row["n_candidates"] or 0
    progress_parts = []
    if n_hypotheses or n_reviews or n_matches:
        progress_parts.append(f"{n_hypotheses} ideas")
        progress_parts.append(f"{n_reviews} reviews")
        progress_parts.append(f"{n_matches} matches")
    if n_assays or n_candidates:
        progress_parts.append(f"{n_assays} assays")
        progress_parts.append(f"{n_candidates} candidates")
    return RunIndexRow(
        session_id=session_id,
        short_id=_short_id(session_id),
        status=row["status"],
        workflow=row["workflow"],
        research_goal=row["research_goal"],
        updated_at=row["updated_at"],
        budget_used_usd=float(row["budget_used_usd"] or 0.0),
        budget_usd=float(row["budget_usd"] or 0.0),
        health_label=(
            f"{row['active_tasks'] or 0} active, "
            f"{row['pending_tasks'] or 0} pending, "
            f"{row['dead_tasks'] or 0} dead"
        ),
        progress_label=", ".join(progress_parts) if progress_parts else "No outputs yet",
        dashboard_path=f"/sessions/{session_id}/dashboard",
        overview_path=f"/sessions/{session_id}/overview" if row["final_overview"] else None,
    )


async def _task_counts(conn: aiosqlite.Connection, session_id: str) -> dict[str, int]:
    async with conn.execute(
        "SELECT status, COUNT(*) AS n FROM tasks WHERE session_id=? GROUP BY status",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {row["status"]: row["n"] for row in rows}


async def _active_task_label(conn: aiosqlite.Connection, session_id: str) -> str:
    async with conn.execute(
        """SELECT agent, action FROM tasks
             WHERE session_id=? AND status IN ('leased','in_progress')
             ORDER BY started_at DESC, created_at DESC LIMIT 1""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return "No active task"
    return f"{row['agent']} / {row['action']}"


async def _retry_count(conn: aiosqlite.Connection, session_id: str) -> int:
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE session_id=? AND attempts > 0",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    return int(row["n"] if row else 0)


async def _scientific_progress(
    cfg: Config,
    conn: aiosqlite.Connection,
    session: Session,
) -> ScientificProgress:
    counts: dict[str, int] = {}
    for key, table in {
        "hypotheses": "hypotheses",
        "reviews": "reviews",
        "matches": "tournament_matches",
        "assays": "assay_proposals",
        "candidates": "therapeutic_candidates",
        "analysis_runs": "analysis_runs",
        "experiment_insights": "experiment_insights",
    }.items():
        async with conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE session_id=?",
            (session.id,),
        ) as cur:
            row = await cur.fetchone()
        counts[key] = int(row["n"] if row else 0)
    evidence_sources = 0
    for artifact in ScientistWorkspace(cfg, session.id).list():
        if artifact.kind == "evidence_bundle":
            evidence_sources += int(artifact.metadata.get("n_local_sources") or 0)
            evidence_sources += int(artifact.metadata.get("n_planned_searches") or 0)
    return ScientificProgress(
        evidence_sources=evidence_sources,
        hypotheses=counts["hypotheses"],
        reviews=counts["reviews"],
        matches=counts["matches"],
        assays=counts["assays"],
        candidates=counts["candidates"],
        analysis_runs=counts["analysis_runs"],
        experiment_insights=counts["experiment_insights"],
        final_overview_ready=session.final_overview is not None,
    )


async def _phase_panels(
    cfg: Config,
    conn: aiosqlite.Connection,
    session: Session,
    progress: ScientificProgress,
    metrics: dict[str, Any],
) -> list[PhasePanel]:
    del cfg
    panels = [
        PhasePanel(
            key="prompt_plan",
            title="Prompt and plan",
            status="ready",
            summary=f"{len(session.research_plan.retrieval_queries)} retrieval queries",
            metrics={
                "constraints": len(session.research_plan.constraints),
                "preferences": len(session.research_plan.preferences),
            },
        ),
        PhasePanel(
            key="evidence",
            title="Evidence",
            status="ready" if progress.evidence_sources else "waiting",
            summary=f"{progress.evidence_sources} planned or local sources",
            metrics={
                "retrieval_calls": metrics["retrieval_tool_calls"],
                "cache_hit_ratio": metrics["retrieval_cache_hit_ratio"],
            },
        ),
    ]
    if session.workflow == "therapeutic_discovery":
        panels.extend([
            PhasePanel(
                key="assays",
                title="Assays",
                status="active" if progress.assays else "waiting",
                summary=f"{progress.assays} assays",
                metrics={"assays": progress.assays},
            ),
            PhasePanel(
                key="candidates",
                title="Candidates",
                status="active" if progress.candidates else "waiting",
                summary=f"{progress.candidates} candidates",
                metrics={"candidates": progress.candidates},
            ),
            PhasePanel(
                key="analysis",
                title="Analysis and insights",
                status="ready" if progress.analysis_runs else "waiting",
                summary=f"{progress.analysis_runs} analyses, {progress.experiment_insights} insights",
                metrics={
                    "analysis_runs": progress.analysis_runs,
                    "experiment_insights": progress.experiment_insights,
                },
            ),
        ])
    else:
        panels.extend([
            PhasePanel(
                key="generation",
                title="Generation",
                status="active" if progress.hypotheses else "waiting",
                summary=f"{progress.hypotheses} hypotheses",
                metrics={
                    "duplicates": metrics["n_duplicate_hypotheses"],
                    "duplicate_rate": metrics["duplicate_rate"],
                },
            ),
            PhasePanel(
                key="review",
                title="Review",
                status="active" if progress.reviews else "waiting",
                summary=f"{progress.reviews} reviews",
                metrics={"reviews": progress.reviews},
            ),
            PhasePanel(
                key="tournament",
                title="Tournament",
                status="active" if progress.matches else "waiting",
                summary=f"{progress.matches} matches",
                metrics={
                    "invalid_matches": metrics["n_invalid_matches"],
                    "ranking_cost_usd": metrics["ranking_cost_usd"],
                },
            ),
            PhasePanel(
                key="evolution",
                title="Evolution and proximity",
                status="waiting",
                summary="Evolution and proximity activity",
                metrics={
                    "duplicates_reaching_tournament": metrics["n_duplicates_reaching_tournament"],
                },
            ),
        ])
    panels.append(PhasePanel(
        key="outputs",
        title="Outputs and artifacts",
        status="ready" if session.final_overview else "pending",
        summary="Final overview ready" if session.final_overview else "Final overview pending",
        metrics={"final_overview_ready": session.final_overview is not None},
        href=f"/sessions/{session.id}/overview" if session.final_overview else None,
    ))
    return panels


def _latest_event_label(recent_events: list[dict[str, Any]]) -> str:
    if not recent_events:
        return "No recent events"
    event = recent_events[0]
    return str(event.get("event") or "event")


def _attention_level(status: str, task_counts: dict[str, int]) -> str:
    if task_counts.get("dead", 0) > 0 or status in {"failed", "aborted"}:
        return "danger"
    if task_counts.get("failed", 0) > 0 or task_counts.get("pending", 0) > 0:
        return "warning"
    if status == "running":
        return "active"
    return "neutral"


def _links(session: Session) -> DashboardLinks:
    return DashboardLinks(
        dashboard_path=f"/sessions/{session.id}/dashboard",
        overview_path=f"/sessions/{session.id}/overview" if session.final_overview else None,
        legacy_detail_path=f"/sessions/{session.id}",
    )


def _short_id(session_id: str) -> str:
    return f"...{session_id[-12:]}"
```

- [ ] **Step 4: Run aggregation tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_summary.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit aggregation slice**

Run:

```bash
git add co_scientist/web/dashboard.py co_scientist/tests/unit/test_dashboard_summary.py
git commit -m "Add dashboard aggregation summaries"
```

Expected: commit succeeds.

---

## Task 2: Runs Index Route And Template

**Files:**
- Modify: `co_scientist/web/app.py`
- Modify: `co_scientist/web/templates/base.html`
- Create: `co_scientist/web/templates/runs.html`
- Test: `co_scientist/tests/unit/test_dashboard_web.py`

- [ ] **Step 1: Write failing route tests for `/runs` and `/`**

Create `co_scientist/tests/unit/test_dashboard_web.py` with the first tests:

```python
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
```

- [ ] **Step 2: Run route tests to verify they fail**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_web.py -q
```

Expected: FAIL because `/runs` is not registered and `/` still renders the old session table.

- [ ] **Step 3: Add runs route wiring**

Modify imports in `co_scientist/web/app.py`:

```python
from .dashboard import runs_index as build_runs_index
```

Replace the existing `index()` route body with:

```python
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return await runs_page(request)

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_page(request: Request) -> HTMLResponse:
        conn = await db_mod.connect(cfg)
        try:
            index_model = await build_runs_index(cfg, conn)
            return TEMPLATES.TemplateResponse(
                request,
                "runs.html",
                {"runs_index": index_model, "runs": index_model.rows},
            )
        finally:
            await conn.close()
```

Keep `_list_sessions()` for now because old code or tests may still use it. Remove it only in a later cleanup when no references remain.

- [ ] **Step 4: Add runs template**

Create `co_scientist/web/templates/runs.html`:

```html
{% extends "base.html" %}
{% block title %}Runs — Discovery Co-Scientist{% endblock %}
{% block content %}
<hgroup>
    <h1>Runs</h1>
    <p>Monitor active runs and review past Discovery Co-Scientist results.</p>
</hgroup>

{% if not runs %}
<article>
    <p>No runs yet.</p>
    <a href="/sessions/new" role="button">Start your first session</a>
</article>
{% else %}
<section class="runs-index">
    <table>
        <thead>
        <tr>
            <th>Run</th>
            <th>Status</th>
            <th>Workflow</th>
            <th>Goal</th>
            <th>Health</th>
            <th>Progress</th>
            <th style="text-align:right">Budget</th>
            <th>Updated</th>
            <th>Links</th>
        </tr>
        </thead>
        <tbody>
        {% for run in runs %}
        <tr>
            <td data-label="Run"><code>{{ run.short_id }}</code></td>
            <td data-label="Status"><span class="status status-{{ run.status }}">{{ run.status }}</span></td>
            <td data-label="Workflow"><code>{{ run.workflow }}</code></td>
            <td data-label="Goal" class="runs-goal">{{ run.research_goal[:140] }}</td>
            <td data-label="Health">{{ run.health_label }}</td>
            <td data-label="Progress">{{ run.progress_label }}</td>
            <td data-label="Budget" style="text-align:right">
                ${{ "%.2f"|format(run.budget_used_usd) }} / ${{ "%.2f"|format(run.budget_usd) }}
            </td>
            <td data-label="Updated"><small>{{ run.updated_at[:19] }}</small></td>
            <td data-label="Links">
                <a href="{{ run.dashboard_path }}">Dashboard</a>
                {% if run.overview_path %}
                    · <a href="{{ run.overview_path }}">Overview</a>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Update base navigation**

Modify `co_scientist/web/templates/base.html` navigation:

```html
<nav class="container-fluid">
    <ul>
        <li><strong><a href="/runs" style="text-decoration:none">Discovery Co-Scientist</a></strong></li>
    </ul>
    <ul>
        <li><a href="/runs">Runs</a></li>
        <li><a href="/sessions/new" role="button">New session</a></li>
    </ul>
</nav>
```

- [ ] **Step 6: Run route tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_web.py -q
```

Expected: PASS for the two runs-index tests.

- [ ] **Step 7: Commit runs index slice**

Run:

```bash
git add co_scientist/web/app.py co_scientist/web/templates/base.html co_scientist/web/templates/runs.html co_scientist/tests/unit/test_dashboard_web.py
git commit -m "Add runs index dashboard"
```

Expected: commit succeeds.

---

## Task 3: Per-Session Dashboard Route And Template

**Files:**
- Modify: `co_scientist/web/app.py`
- Create: `co_scientist/web/templates/session_dashboard.html`
- Modify: `co_scientist/tests/unit/test_dashboard_web.py`

- [ ] **Step 1: Add failing per-session route tests**

Append to `co_scientist/tests/unit/test_dashboard_web.py`:

```python
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
    assert "data-refresh-enabled=\"false\"" in response.text


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
    assert "Analysis and insights" in response.text
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_web.py -q
```

Expected: FAIL because the dashboard route and template do not exist.

- [ ] **Step 3: Add session dashboard route**

Modify `co_scientist/web/app.py` imports:

```python
from .dashboard import (
    dashboard_to_dict,
    runs_index as build_runs_index,
    session_dashboard as build_session_dashboard,
)
```

Add this route near the existing session detail route:

```python
    @app.get("/sessions/{session_id}/dashboard", response_class=HTMLResponse)
    async def session_dashboard_page(request: Request, session_id: str) -> HTMLResponse:
        conn = await db_mod.connect(cfg)
        try:
            try:
                dashboard = await build_session_dashboard(cfg, conn, session_id)
            except KeyError as e:
                raise HTTPException(status_code=404, detail="session not found") from e
            return TEMPLATES.TemplateResponse(
                request,
                "session_dashboard.html",
                {"dashboard": dashboard},
            )
        finally:
            await conn.close()
```

- [ ] **Step 4: Add per-session dashboard template**

Create `co_scientist/web/templates/session_dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}Dashboard {{ dashboard.short_id }} — Discovery Co-Scientist{% endblock %}
{% block content %}
<section class="dashboard-shell"
         data-session-id="{{ dashboard.session.id }}"
         data-refresh-enabled="{{ 'true' if dashboard.refresh.enabled else 'false' }}"
         data-summary-url="/api/sessions/{{ dashboard.session.id }}/dashboard-summary">
    <hgroup>
        <h1>Dashboard <code>{{ dashboard.short_id }}</code></h1>
        <p>
            <span class="status status-{{ dashboard.session.status }}">{{ dashboard.session.status }}</span>
            workflow: <code>{{ dashboard.session.workflow }}</code>
            · {{ dashboard.session.research_goal[:220] }}
        </p>
    </hgroup>

    <div class="dashboard-top">
        <article class="dashboard-card dashboard-health attention-{{ dashboard.run_health.attention_level }}"
                 hx-get="/api/sessions/{{ dashboard.session.id }}/dashboard-summary"
                 hx-trigger="every {{ dashboard.refresh.health_interval_ms }}ms [document.querySelector('.dashboard-shell').dataset.refreshEnabled == 'true']"
                 hx-swap="none">
            <header>
                <strong>Run health</strong>
                <small>{{ dashboard.run_health.latest_event_label }}</small>
            </header>
            <div class="metric-grid">
                <div><strong>{{ dashboard.run_health.task_counts.get("in_progress", 0) + dashboard.run_health.task_counts.get("leased", 0) }}</strong><span>active</span></div>
                <div><strong>{{ dashboard.run_health.task_counts.get("pending", 0) }}</strong><span>pending</span></div>
                <div><strong>{{ dashboard.run_health.task_counts.get("done", 0) }}</strong><span>done</span></div>
                <div><strong>{{ dashboard.run_health.dead_tasks }}</strong><span>dead</span></div>
            </div>
            <p><small>Current: {{ dashboard.run_health.active_task_label }}</small></p>
            <div class="dashboard-actions">
                <button hx-post="/api/sessions/{{ dashboard.session.id }}/pause" hx-swap="none">Pause</button>
                <button hx-post="/api/sessions/{{ dashboard.session.id }}/resume" hx-swap="none" class="secondary">Resume</button>
                <button hx-post="/api/sessions/{{ dashboard.session.id }}/abort" hx-swap="none" hx-confirm="Abort this session?" class="contrast">Abort</button>
            </div>
        </article>

        <article class="dashboard-card">
            <header><strong>Budget and time</strong></header>
            <p>${{ "%.2f"|format(dashboard.budget_time.cost_used_usd) }} / ${{ "%.2f"|format(dashboard.budget_time.cost_budget_usd) }}</p>
            <progress value="{{ dashboard.budget_time.cost_used_usd }}" max="{{ dashboard.budget_time.cost_budget_usd }}"></progress>
            <p><small>{{ dashboard.budget_time.llm_calls }} calls · p95 {% if dashboard.budget_time.p95_latency_ms %}{{ "%.0f"|format(dashboard.budget_time.p95_latency_ms) }}ms{% else %}unavailable{% endif %}</small></p>
        </article>

        <article class="dashboard-card">
            <header><strong>Scientific progress</strong></header>
            <div class="metric-grid compact">
                <div><strong>{{ dashboard.scientific_progress.evidence_sources }}</strong><span>sources</span></div>
                <div><strong>{{ dashboard.scientific_progress.hypotheses or dashboard.scientific_progress.candidates }}</strong><span>ideas</span></div>
                <div><strong>{{ dashboard.scientific_progress.reviews }}</strong><span>reviews</span></div>
                <div><strong>{{ dashboard.scientific_progress.matches }}</strong><span>matches</span></div>
            </div>
        </article>
    </div>

    <section>
        <h2>Phases</h2>
        <div class="phase-grid">
            {% for panel in dashboard.phase_panels %}
            <article class="phase-panel phase-{{ panel.status }}">
                <header>
                    <strong>{{ panel.title }}</strong>
                    <span class="phase-status">{{ panel.status }}</span>
                </header>
                <p>{{ panel.summary }}</p>
                {% if panel.metrics %}
                <dl class="panel-metrics">
                    {% for key, value in panel.metrics.items() %}
                    <div>
                        <dt>{{ key.replace("_", " ") }}</dt>
                        <dd>{{ value if value is not none else "—" }}</dd>
                    </div>
                    {% endfor %}
                </dl>
                {% endif %}
                {% if panel.href %}
                <a href="{{ panel.href }}">Open</a>
                {% endif %}
            </article>
            {% endfor %}
        </div>
    </section>

    <section>
        <h2>Recent events</h2>
        <ul id="events-log" class="events-log">
            {% for event in dashboard.recent_events %}
            <li><small>{{ event.event }}</small> {{ event.payload }}</li>
            {% else %}
            <li><em>No recent events.</em></li>
            {% endfor %}
        </ul>
    </section>
</section>

<script>
(function() {
    const shell = document.querySelector(".dashboard-shell");
    if (!shell || shell.dataset.refreshEnabled !== "true") return;
    const log = document.getElementById("events-log");
    const es = new EventSource("/api/sessions/{{ dashboard.session.id }}/events");
    function push(name, payload) {
        const li = document.createElement("li");
        li.textContent = new Date().toISOString().slice(11, 19) + " " + name +
                         " " + (payload ? JSON.stringify(payload).slice(0, 180) : "");
        log.insertBefore(li, log.firstChild);
        while (log.children.length > 80) log.removeChild(log.lastChild);
    }
    ["session_started","project_files_ingested","evidence_bundle_created",
     "task_started","task_completed","task_failed","hypothesis_created",
     "review_completed","tournament_match_complete","session_done",
     "human_feedback","session_paused","session_resumed","session_aborted"].forEach(name => {
        es.addEventListener(name, e => {
            try { push(name, JSON.parse(e.data)); }
            catch { push(name, e.data); }
        });
    });
    es.onerror = () => push("[sse error]", null);
})();
</script>
{% endblock %}
```

- [ ] **Step 5: Run per-session dashboard tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_web.py -q
```

Expected: PASS for route and rendering tests.

- [ ] **Step 6: Commit dashboard page slice**

Run:

```bash
git add co_scientist/web/app.py co_scientist/web/templates/session_dashboard.html co_scientist/tests/unit/test_dashboard_web.py
git commit -m "Add per-session run dashboard"
```

Expected: commit succeeds.

---

## Task 4: JSON Refresh Endpoint And htmx-Safe Summary

**Files:**
- Modify: `co_scientist/web/app.py`
- Modify: `co_scientist/web/dashboard.py`
- Modify: `co_scientist/tests/unit/test_dashboard_web.py`

- [ ] **Step 1: Add failing JSON endpoint test**

Append to `co_scientist/tests/unit/test_dashboard_web.py`:

```python
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
```

- [ ] **Step 2: Run endpoint tests to verify failure**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_web.py::test_dashboard_summary_endpoint_returns_structured_json co_scientist/tests/unit/test_dashboard_web.py::test_dashboard_summary_endpoint_returns_404_for_missing_session -q
```

Expected: FAIL because the endpoint is not registered.

- [ ] **Step 3: Make dashboard JSON serializable**

If `dashboard_to_dict()` returns raw `datetime` values that fail JSON encoding, update it in `co_scientist/web/dashboard.py`:

```python
def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def dashboard_to_dict(dashboard: SessionDashboard) -> dict[str, Any]:
    payload = {
        "session": dashboard.session.model_dump(mode="json"),
        "short_id": dashboard.short_id,
        "run_health": dashboard.run_health.__dict__,
        "budget_time": dashboard.budget_time.__dict__,
        "scientific_progress": dashboard.scientific_progress.__dict__,
        "phase_panels": [panel.__dict__ for panel in dashboard.phase_panels],
        "recent_events": dashboard.recent_events,
        "links": dashboard.links.__dict__,
        "refresh": dashboard.refresh.__dict__,
        "metrics": dashboard.metrics,
    }
    return _jsonable(payload)
```

- [ ] **Step 4: Add JSON endpoint**

Add to `co_scientist/web/app.py` near the existing API routes:

```python
    @app.get("/api/sessions/{session_id}/dashboard-summary")
    async def api_dashboard_summary(session_id: str) -> JSONResponse:
        conn = await db_mod.connect(cfg)
        try:
            try:
                dashboard = await build_session_dashboard(cfg, conn, session_id)
            except KeyError as e:
                raise HTTPException(status_code=404, detail="session not found") from e
            return JSONResponse(dashboard_to_dict(dashboard))
        finally:
            await conn.close()
```

- [ ] **Step 5: Run endpoint tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_web.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit refresh endpoint slice**

Run:

```bash
git add co_scientist/web/app.py co_scientist/web/dashboard.py co_scientist/tests/unit/test_dashboard_web.py
git commit -m "Add dashboard summary endpoint"
```

Expected: commit succeeds.

---

## Task 5: CLI Dashboard Links

**Files:**
- Modify: `co_scientist/cli.py`
- Create: `co_scientist/tests/unit/test_cli_dashboard_links.py`

- [ ] **Step 1: Write failing CLI helper tests**

Create `co_scientist/tests/unit/test_cli_dashboard_links.py`:

```python
"""Tests for CLI dashboard link helpers."""

from __future__ import annotations

from co_scientist.cli import _dashboard_base_url, _dashboard_links
from co_scientist.config import Config, WebUICfg


def test_dashboard_base_url_uses_localhost_for_loopback() -> None:
    cfg = Config(web_ui=WebUICfg(host="127.0.0.1", port=7878))

    assert _dashboard_base_url(cfg) == "http://localhost:7878"


def test_dashboard_base_url_uses_configured_host_for_non_loopback() -> None:
    cfg = Config(web_ui=WebUICfg(host="0.0.0.0", port=9000))

    assert _dashboard_base_url(cfg) == "http://localhost:9000"


def test_dashboard_links_include_runs_and_session_url() -> None:
    cfg = Config(web_ui=WebUICfg(host="127.0.0.1", port=7878))

    links = _dashboard_links(cfg, "ses_cli_dash")

    assert links["runs"] == "http://localhost:7878/runs"
    assert links["session"] == "http://localhost:7878/sessions/ses_cli_dash/dashboard"
    assert links["serve_command"] == "discovery-coscientist serve"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_cli_dashboard_links.py -q
```

Expected: FAIL because `_dashboard_base_url` and `_dashboard_links` are missing.

- [ ] **Step 3: Add CLI dashboard helpers**

Add to `co_scientist/cli.py` near the global constants:

```python
def _dashboard_base_url(cfg) -> str:
    host = cfg.web_ui.host
    display_host = "localhost" if host in {"127.0.0.1", "0.0.0.0", "::1"} else host
    return f"http://{display_host}:{cfg.web_ui.port}"


def _dashboard_links(cfg, session_id: str | None = None) -> dict[str, str | None]:
    base = _dashboard_base_url(cfg)
    return {
        "runs": f"{base}/runs",
        "session": f"{base}/sessions/{session_id}/dashboard" if session_id else None,
        "serve_command": f"{PRIMARY_CLI} serve",
    }


def _print_dashboard_links(cfg, session_id: str | None = None) -> None:
    links = _dashboard_links(cfg, session_id)
    console.print(f"Runs dashboard: {links['runs']}")
    if links["session"]:
        console.print(f"This run:       {links['session']}")
    console.print(f"[dim]Start dashboard server: {links['serve_command']}[/dim]")
```

- [ ] **Step 4: Print links from CLI commands**

In `run()` before starting the supervisor, after pre-flight estimate output, add:

```python
    _print_dashboard_links(cfg)
```

After `session_id = asyncio.run(...)`, replace final prints with:

```python
    console.print(f"[green]Done.[/green] session={session_id}")
    _print_dashboard_links(cfg, session_id)
    console.print(f"View report:  {PRIMARY_CLI} report {session_id}")
```

In `resume()`, after `sid = asyncio.run(...)`, add:

```python
    console.print(f"[green]Done.[/green] session={sid}")
    _print_dashboard_links(cfg, sid)
```

In `evidence()`, after the session ID is available, add:

```python
    _print_dashboard_links(cfg, session_id)
```

- [ ] **Step 5: Run CLI helper tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_cli_dashboard_links.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit CLI link slice**

Run:

```bash
git add co_scientist/cli.py co_scientist/tests/unit/test_cli_dashboard_links.py
git commit -m "Print dashboard links from CLI"
```

Expected: commit succeeds.

---

## Task 6: Dashboard Styling, Regression Tests, And Final Verification

**Files:**
- Modify: `co_scientist/web/static/app.css`
- Modify: `co_scientist/tests/unit/test_dashboard_web.py`
- Optional Modify: `README.md`

- [ ] **Step 1: Add rendering assertions for dashboard CSS hooks**

Append to `co_scientist/tests/unit/test_dashboard_web.py`:

```python
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
```

- [ ] **Step 2: Run CSS hook test**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_web.py::test_dashboard_pages_expose_stable_css_hooks -q
```

Expected: PASS if Task 2 and Task 3 templates are in place.

- [ ] **Step 3: Add dashboard CSS**

Append to `co_scientist/web/static/app.css`:

```css
.runs-index table {
    table-layout: fixed;
    width: 100%;
}
.runs-goal {
    max-width: 28rem;
    overflow-wrap: anywhere;
}
.dashboard-shell h1 code {
    font-size: 0.8em;
}
.dashboard-top {
    display: grid;
    grid-template-columns: minmax(20rem, 2fr) minmax(14rem, 1fr) minmax(14rem, 1fr);
    gap: 1rem;
    align-items: stretch;
}
.dashboard-card {
    border-radius: 0.35rem;
}
.dashboard-health {
    border-width: 2px;
}
.attention-danger {
    border-color: #b53636;
}
.attention-warning {
    border-color: #c08a00;
}
.attention-active {
    border-color: #2864c5;
}
.metric-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.75rem;
}
.metric-grid div {
    min-width: 0;
}
.metric-grid strong {
    display: block;
    font-size: 1.45rem;
    line-height: 1.1;
}
.metric-grid span {
    display: block;
    color: var(--pico-muted-color);
    font-size: 0.82rem;
}
.metric-grid.compact strong {
    font-size: 1.2rem;
}
.dashboard-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.75rem;
}
.dashboard-actions button {
    width: auto;
}
.phase-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1rem;
}
.phase-panel {
    border-radius: 0.35rem;
}
.phase-panel header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.phase-status {
    margin-left: auto;
    color: var(--pico-muted-color);
    font-family: monospace;
    font-size: 0.8rem;
}
.panel-metrics {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.45rem 0.75rem;
}
.panel-metrics div {
    min-width: 0;
}
.panel-metrics dt {
    color: var(--pico-muted-color);
    font-size: 0.75rem;
}
.panel-metrics dd {
    margin: 0;
    overflow-wrap: anywhere;
}
.events-log {
    font-family: monospace;
    font-size: 0.85rem;
    max-height: 16rem;
    overflow: auto;
}
.events-log li {
    padding: 0.2rem 0.25rem;
    border-bottom: 1px dotted var(--pico-muted-border-color);
}
@media (max-width: 980px) {
    .dashboard-top,
    .phase-grid {
        grid-template-columns: 1fr;
    }
}
@media (max-width: 760px) {
    .runs-index table,
    .runs-index tbody,
    .runs-index tr,
    .runs-index td {
        display: block;
        width: 100%;
    }
    .runs-index thead {
        display: none;
    }
    .runs-index tr {
        border: 1px solid var(--pico-muted-border-color);
        border-radius: 0.35rem;
        margin-bottom: 0.75rem;
        padding: 0.45rem 0.65rem;
    }
    .runs-index td {
        border-bottom: 0;
        padding: 0.35rem 0;
    }
    .runs-index td::before {
        content: attr(data-label);
        display: block;
        font-size: 0.75rem;
        font-weight: 700;
        color: var(--pico-muted-color);
        margin-bottom: 0.1rem;
    }
    .metric-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}
```

- [ ] **Step 4: Update README web UI section**

Modify the README web UI paragraph under "Run the web UI" to include:

```markdown
The Runs dashboard at `http://localhost:7878/runs` lists active and past sessions.
Each row links to a per-session dashboard for live monitoring and historical review.
```

- [ ] **Step 5: Run focused dashboard tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_dashboard_summary.py co_scientist/tests/unit/test_dashboard_web.py co_scientist/tests/unit/test_cli_dashboard_links.py -q
```

Expected: PASS.

- [ ] **Step 6: Run existing unit suite**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit -q
```

Expected: PASS. If unrelated existing tests fail, record the failing test names and the failure message before deciding whether the dashboard change caused the regression.

- [ ] **Step 7: Commit final styling and verification slice**

Run:

```bash
git add co_scientist/web/static/app.css co_scientist/tests/unit/test_dashboard_web.py README.md
git commit -m "Polish dashboard layout"
```

Expected: commit succeeds.

---

## Verification Checklist

- `/runs` lists all runs without requiring a full session ID.
- `/` reaches the same run list or equivalent content.
- Active runs are listed before completed runs.
- Each run has a full dashboard link.
- Per-session dashboard renders for running, done, failed, aborted, and therapeutic discovery sessions.
- Run health is the first visual priority.
- Completed sessions do not poll by default.
- Running sessions include SSE event feed and refresh metadata.
- CLI prints the global runs link and per-run dashboard link when available.
- No API keys are required for dashboard tests.
- The implementation does not introduce a new metrics store or frontend build step.

## Plan Self-Review

Spec coverage:

- Canonical run index is implemented in Task 2.
- Per-session command center is implemented in Task 3.
- Refresh endpoint and SSE/hx affordances are implemented in Task 4.
- CLI dashboard links are implemented in Task 5.
- Styling, route coverage, and final verification are implemented in Task 6.
- Aggregation over existing SQLite and workspace state is implemented in Task 1.

Placeholder scan:

- This plan contains no unresolved placeholders.
- Code snippets use concrete paths, commands, and expected results.

Type consistency:

- `runs_index()` and `session_dashboard()` are defined in Task 1 and reused consistently in later tasks.
- `dashboard_to_dict()` is defined in Task 1 and refined in Task 4.
- Route names and URLs match the approved spec: `/runs`, `/`, `/sessions/{session_id}/dashboard`, and `/api/sessions/{session_id}/dashboard-summary`.
