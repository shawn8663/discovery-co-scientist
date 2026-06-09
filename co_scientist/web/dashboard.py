"""Read-only dashboard aggregation view models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite
from pydantic import BaseModel, ValidationError

from ..config import Config
from ..models import ResearchPlan, Session
from ..obs.metrics import session_metrics_cached
from ..obs.metrics import to_dict as metrics_to_dict
from ..storage.repos import robin as robin_repo
from ..storage.repos import sessions as sess_repo
from ..storage.repos import tasks as task_repo
from ..workspace import ScientistWorkspace, WorkspaceArtifact

ACTIVE_STATUSES = {"running", "paused"}
STATIC_STATUSES = {"done", "failed", "aborted"}
GENERAL_PANEL_KEYS = (
    "prompt_plan",
    "evidence",
    "generation",
    "review",
    "tournament",
    "evolution",
    "outputs",
)
THERAPEUTIC_PANEL_KEYS = (
    "prompt_plan",
    "evidence",
    "assays",
    "candidates",
    "analysis",
    "outputs",
)


@dataclass(frozen=True)
class DashboardLinks:
    session_path: str
    dashboard_path: str
    overview_path: str | None = None


@dataclass(frozen=True)
class RunHealth:
    status: str
    task_counts: dict[str, int]
    active_tasks: int
    pending_tasks: int
    done_tasks: int
    dead_tasks: int
    retry_count: int
    current_tasks: list[dict[str, Any]]
    latest_activity_at: datetime | None
    latest_event_age_seconds: float | None
    attention_level: str


@dataclass(frozen=True)
class BudgetTime:
    cost_used_usd: float
    cost_budget_usd: float
    tokens_used: int
    token_budget: int
    calls: int
    elapsed_seconds: float | None
    wall_deadline: datetime | None
    wall_remaining_seconds: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None


@dataclass(frozen=True)
class ScientificProgress:
    evidence_sources: int
    hypotheses: int
    reviewed: int
    reviews: int
    tournament_matches: int
    assays: int = 0
    assay_evaluations: int = 0
    candidates: int = 0
    candidate_evaluations: int = 0
    analysis_runs: int = 0
    experiment_insights: int = 0
    final_output_ready: bool = False


@dataclass(frozen=True)
class PhasePanel:
    key: str
    title: str
    state: str
    summary: str
    counts: dict[str, int | float]
    items: list[dict[str, Any]]
    links: dict[str, str]


@dataclass(frozen=True)
class RefreshPolicy:
    enabled: bool
    interval_ms: int | None
    sse_path: str | None


@dataclass(frozen=True)
class SessionDashboard:
    session: Session
    links: DashboardLinks
    run_health: RunHealth
    budget_time: BudgetTime
    scientific_progress: ScientificProgress
    phase_panels: list[PhasePanel]
    refresh: RefreshPolicy
    metrics: dict[str, Any]
    evidence_artifacts: list[WorkspaceArtifact]


@dataclass(frozen=True)
class RunIndexRow:
    session_id: str
    short_id: str
    status: str
    workflow: str
    research_goal: str
    updated_at: datetime
    budget_used_usd: float
    budget_usd: float
    attention_level: str
    health_summary: str
    scientific_summary: str
    dashboard_path: str
    overview_path: str | None
    final_overview_available: bool


@dataclass(frozen=True)
class RunIndex:
    rows: list[RunIndexRow]
    generated_at: datetime


async def runs_index(cfg: Config, conn: aiosqlite.Connection) -> RunIndex:
    """Build the global run list with active runs pinned above historical runs."""
    _ = cfg
    async with conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC") as cur:
        rows = await cur.fetchall()
    sessions = [_row_to_session(row) for row in rows]
    session_ids = [session.id for session in sessions]
    task_counts_by_session = await _task_counts_for_sessions(conn, session_ids)
    science_counts_by_session = await _science_counts_for_sessions(conn, session_ids)
    sessions.sort(
        key=lambda s: (0 if s.status in ACTIVE_STATUSES else 1, -s.updated_at.timestamp())
    )

    index_rows: list[RunIndexRow] = []
    for session in sessions:
        task_counts = task_counts_by_session.get(session.id, {})
        science_counts = science_counts_by_session.get(session.id, {})
        dead = task_counts.get("dead", 0)
        active = task_counts.get("in_progress", 0) + task_counts.get("leased", 0)
        pending = task_counts.get("pending", 0)
        attention = _attention_level(session.status, task_counts)
        index_rows.append(
            RunIndexRow(
                session_id=session.id,
                short_id=_short_id(session.id),
                status=session.status,
                workflow=session.workflow,
                research_goal=session.research_goal,
                updated_at=session.updated_at,
                budget_used_usd=session.budget_used_usd,
                budget_usd=session.budget_usd,
                attention_level=attention,
                health_summary=f"{active} active, {pending} pending, {dead} failed",
                scientific_summary=_index_scientific_summary(session.workflow, science_counts),
                dashboard_path=_dashboard_path(session.id),
                overview_path=_overview_path(session),
                final_overview_available=bool(session.final_overview),
            )
        )
    return RunIndex(rows=index_rows, generated_at=datetime.now(UTC))


async def session_dashboard(
    cfg: Config, conn: aiosqlite.Connection, session_id: str
) -> SessionDashboard:
    """Build a per-session dashboard summary from existing tables and artifacts."""
    session = await sess_repo.fetch(conn, session_id)
    if session is None:
        raise KeyError(f"session not found: {session_id}")

    metrics = metrics_to_dict(await session_metrics_cached(conn, session.id))
    task_counts = await task_repo.count_by_status(conn, session.id)
    current_tasks = await _current_tasks(conn, session.id)
    retry_count = await _task_attempts(conn, session.id)
    latest_activity_at = await _latest_activity_at(conn, session)
    phase_task_counts = await _task_phase_counts(conn, session.id)
    evidence_artifacts = _evidence_artifacts(cfg, session.id)

    assays = (
        await robin_repo.list_assays(conn, session.id)
        if session.workflow == "therapeutic_discovery"
        else []
    )
    candidates = (
        await robin_repo.list_candidates(conn, session.id)
        if session.workflow == "therapeutic_discovery"
        else []
    )
    assay_evaluations = (
        await _count(conn, "assay_evaluations", session.id)
        if session.workflow == "therapeutic_discovery"
        else 0
    )
    candidate_evaluations = (
        await _count(conn, "therapeutic_candidate_evaluations", session.id)
        if session.workflow == "therapeutic_discovery"
        else 0
    )
    analysis_runs = (
        await _count(conn, "analysis_runs", session.id)
        if session.workflow == "therapeutic_discovery"
        else 0
    )
    experiment_insights = (
        await _count(conn, "experiment_insights", session.id)
        if session.workflow == "therapeutic_discovery"
        else 0
    )
    reviews = await _count(conn, "reviews", session.id)

    progress = ScientificProgress(
        evidence_sources=len(evidence_artifacts) or int(metrics["retrieval_tool_calls"]),
        hypotheses=int(metrics["n_hypotheses"]),
        reviewed=int(metrics["n_reviewed"]),
        reviews=reviews,
        tournament_matches=int(metrics["n_matches"]),
        assays=len(assays),
        assay_evaluations=assay_evaluations,
        candidates=len(candidates),
        candidate_evaluations=candidate_evaluations,
        analysis_runs=analysis_runs,
        experiment_insights=experiment_insights,
        final_output_ready=bool(session.final_overview),
    )
    dashboard = SessionDashboard(
        session=session,
        links=DashboardLinks(
            session_path=f"/sessions/{session.id}",
            dashboard_path=_dashboard_path(session.id),
            overview_path=_overview_path(session),
        ),
        run_health=_run_health(
            session, task_counts, current_tasks, latest_activity_at, retry_count
        ),
        budget_time=_budget_time(session, metrics),
        scientific_progress=progress,
        phase_panels=_phase_panels(
            session=session,
            metrics=metrics,
            progress=progress,
            evidence_artifacts=evidence_artifacts,
            phase_task_counts=phase_task_counts,
            assays=assays,
            candidates=candidates,
        ),
        refresh=RefreshPolicy(
            enabled=session.status not in STATIC_STATUSES,
            interval_ms=3000 if session.status in ACTIVE_STATUSES else None,
            sse_path=f"/api/sessions/{session.id}/events" if session.status in ACTIVE_STATUSES else None,
        ),
        metrics=metrics,
        evidence_artifacts=evidence_artifacts,
    )
    return dashboard


def dashboard_to_dict(dashboard: SessionDashboard) -> dict[str, Any]:
    """Return a JSON-safe dictionary for API responses."""
    return {
        "session": _public_session_dict(dashboard.session),
        "links": _json_safe(dashboard.links),
        "run_health": _json_safe(dashboard.run_health),
        "budget_time": _json_safe(dashboard.budget_time),
        "scientific_progress": _json_safe(dashboard.scientific_progress),
        "phase_panels": _json_safe(dashboard.phase_panels),
        "refresh": _json_safe(dashboard.refresh),
        "metrics": _json_safe(dashboard.metrics),
        "evidence_artifacts": _json_safe(dashboard.evidence_artifacts),
    }


def _public_session_dict(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "created_at": _json_safe(session.created_at),
        "updated_at": _json_safe(session.updated_at),
        "status": session.status,
        "workflow": session.workflow,
        "research_goal": session.research_goal,
        "final_overview_available": bool(session.final_overview),
    }


def _run_health(
    session: Session,
    task_counts: dict[str, int],
    current_tasks: list[dict[str, Any]],
    latest_activity_at: datetime | None,
    retry_count: int,
) -> RunHealth:
    active = task_counts.get("in_progress", 0) + task_counts.get("leased", 0)
    pending = task_counts.get("pending", 0)
    done = task_counts.get("done", 0)
    dead = task_counts.get("dead", 0)
    latest_age = None
    if latest_activity_at is not None:
        latest_age = max(0.0, (datetime.now(UTC) - _as_utc(latest_activity_at)).total_seconds())
    return RunHealth(
        status=session.status,
        task_counts={
            status: task_counts.get(status, 0)
            for status in _task_statuses(task_counts)
        },
        active_tasks=active,
        pending_tasks=pending,
        done_tasks=done,
        dead_tasks=dead,
        retry_count=retry_count,
        current_tasks=current_tasks,
        latest_activity_at=latest_activity_at,
        latest_event_age_seconds=latest_age,
        attention_level=_attention_level(session.status, task_counts),
    )


def _budget_time(session: Session, metrics: dict[str, Any]) -> BudgetTime:
    now = datetime.now(UTC)
    created_at = _as_utc(session.created_at)
    wall_deadline = _as_utc(session.wall_deadline) if session.wall_deadline else None
    return BudgetTime(
        cost_used_usd=session.budget_used_usd,
        cost_budget_usd=session.budget_usd,
        tokens_used=session.budget_used_tokens,
        token_budget=session.budget_tokens,
        calls=int(metrics["n_calls"]),
        elapsed_seconds=max(0.0, (now - created_at).total_seconds()),
        wall_deadline=wall_deadline,
        wall_remaining_seconds=(
            max(0.0, (wall_deadline - now).total_seconds()) if wall_deadline else None
        ),
        p50_latency_ms=metrics["p50_latency_ms"],
        p95_latency_ms=metrics["p95_latency_ms"],
    )


def _phase_panels(
    *,
    session: Session,
    metrics: dict[str, Any],
    progress: ScientificProgress,
    evidence_artifacts: list[WorkspaceArtifact],
    phase_task_counts: dict[str, dict[str, int]],
    assays: list[Any],
    candidates: list[Any],
) -> list[PhasePanel]:
    keys = THERAPEUTIC_PANEL_KEYS if session.workflow == "therapeutic_discovery" else GENERAL_PANEL_KEYS
    return [
        _panel_for_key(
            key,
            session=session,
            metrics=metrics,
            progress=progress,
            evidence_artifacts=evidence_artifacts,
            phase_task_counts=phase_task_counts,
            assays=assays,
            candidates=candidates,
        )
        for key in keys
    ]


def _panel_for_key(
    key: str,
    *,
    session: Session,
    metrics: dict[str, Any],
    progress: ScientificProgress,
    evidence_artifacts: list[WorkspaceArtifact],
    phase_task_counts: dict[str, dict[str, int]],
    assays: list[Any],
    candidates: list[Any],
) -> PhasePanel:
    plan = session.research_plan
    if key == "prompt_plan":
        counts = {
            "preferences": len(plan.preferences),
            "constraints": len(plan.constraints),
            "idea_attributes": len(plan.idea_attributes),
            "retrieval_queries": len(plan.retrieval_queries),
        }
        items = [
            {"label": "objective", "value": plan.objective},
            {"label": "domain_hint", "value": plan.domain_hint},
            {"label": "clinical_or_translational", "value": plan.clinical_or_translational},
        ]
        return _panel(key, "Prompt and plan", "ready", plan.objective, counts, items, session)
    if key == "evidence":
        counts = {
            "artifacts": len(evidence_artifacts),
            "retrieval_calls": int(metrics["retrieval_tool_calls"]),
            "sources": progress.evidence_sources,
        }
        items = [_artifact_item(artifact) for artifact in evidence_artifacts[:5]]
        return _panel(
            key,
            "Evidence",
            _state_from_count(progress.evidence_sources),
            "Evidence artifacts and retrieval activity",
            counts,
            items,
            session,
        )
    if key == "generation":
        counts = {
            "hypotheses": progress.hypotheses,
            "duplicates": int(metrics["n_duplicate_hypotheses"]),
            "done_tasks": _phase_status_count(phase_task_counts, key, "done"),
        }
        return _panel(
            key,
            "Generation",
            _state_from_count(progress.hypotheses),
            f"{progress.hypotheses} hypotheses generated",
            counts,
            [],
            session,
        )
    if key == "review":
        counts = {
            "reviews": progress.reviews,
            "reviewed": progress.reviewed,
            "done_tasks": _phase_status_count(phase_task_counts, key, "done"),
        }
        return _panel(
            key,
            "Review",
            _state_from_count(progress.reviews),
            f"{progress.reviews} reviews completed",
            counts,
            [],
            session,
        )
    if key == "tournament":
        counts = {
            "matches": progress.tournament_matches,
            "invalid_matches": int(metrics["n_invalid_matches"]),
            "done_tasks": _phase_status_count(phase_task_counts, key, "done"),
        }
        return _panel(
            key,
            "Tournament",
            _state_from_count(progress.tournament_matches),
            f"{progress.tournament_matches} matches completed",
            counts,
            [],
            session,
        )
    if key == "evolution":
        counts = {"done_tasks": _phase_status_count(phase_task_counts, key, "done")}
        return _panel(key, "Evolution", "waiting", "Evolution and proximity updates", counts, [], session)
    if key == "assays":
        counts = {
            "assays": progress.assays,
            "evaluations": progress.assay_evaluations,
            "done_tasks": _phase_status_count(phase_task_counts, key, "done"),
        }
        items = [
            {
                "id": assay.id,
                "title": assay.strategy_name,
                "state": assay.state,
                "rank_score": assay.rank_score,
                "artifact_path": assay.artifact_path,
            }
            for assay in assays[:5]
        ]
        return _panel(
            key,
            "Assays",
            _state_from_count(progress.assays),
            f"{progress.assays} assays proposed",
            counts,
            items,
            session,
        )
    if key == "candidates":
        counts = {
            "candidates": progress.candidates,
            "evaluations": progress.candidate_evaluations,
            "done_tasks": _phase_status_count(phase_task_counts, key, "done"),
        }
        items = [
            {
                "id": candidate.id,
                "title": candidate.candidate,
                "state": candidate.state,
                "rank_score": candidate.rank_score,
                "artifact_path": candidate.artifact_path,
            }
            for candidate in candidates[:5]
        ]
        return _panel(
            key,
            "Candidates",
            _state_from_count(progress.candidates),
            f"{progress.candidates} candidates generated",
            counts,
            items,
            session,
        )
    if key == "analysis":
        counts = {
            "analysis_runs": progress.analysis_runs,
            "experiment_insights": progress.experiment_insights,
            "done_tasks": _phase_status_count(phase_task_counts, key, "done"),
        }
        return _panel(
            key,
            "Analysis",
            _state_from_count(progress.analysis_runs),
            "Experimental analysis and interpretation",
            counts,
            [],
            session,
        )
    if key == "outputs":
        counts = {"final_output_ready": int(progress.final_output_ready)}
        state = "ready" if progress.final_output_ready else "waiting"
        summary = (
            "Final overview available"
            if progress.final_output_ready
            else "Final overview pending"
        )
        return _panel(key, "Outputs", state, summary, counts, [], session)
    return _panel(key, key.replace("_", " ").title(), "waiting", "", {}, [], session)


def _panel(
    key: str,
    title: str,
    state: str,
    summary: str,
    counts: dict[str, int | float],
    items: list[dict[str, Any]],
    session: Session,
) -> PhasePanel:
    links = {"session": f"/sessions/{session.id}"}
    if session.final_overview:
        links["overview"] = f"/sessions/{session.id}/overview"
    return PhasePanel(
        key=key,
        title=title,
        state=state,
        summary=summary,
        counts=counts,
        items=items,
        links=links,
    )


def _artifact_item(artifact: WorkspaceArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "title": artifact.title,
        "path": artifact.path,
    }


async def _task_counts_for_sessions(
    conn: aiosqlite.Connection, session_ids: list[str]
) -> dict[str, dict[str, int]]:
    if not session_ids:
        return {}
    async with conn.execute(
        """SELECT session_id, status, COUNT(*) AS n
              FROM tasks
             GROUP BY session_id, status"""
    ) as cur:
        rows = await cur.fetchall()
    session_id_set = set(session_ids)
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["session_id"] in session_id_set:
            out.setdefault(row["session_id"], {})[row["status"]] = int(row["n"])
    return out


async def _science_counts_for_sessions(
    conn: aiosqlite.Connection, session_ids: list[str]
) -> dict[str, dict[str, int]]:
    if not session_ids:
        return {}
    session_id_set = set(session_ids)
    out: dict[str, dict[str, int]] = {session_id: {} for session_id in session_ids}

    async with conn.execute(
        """SELECT session_id,
                   COUNT(*) AS hypotheses,
                   SUM(CASE WHEN state IN ('reviewed','in_tournament','pinned')
                            THEN 1 ELSE 0 END) AS reviewed
              FROM hypotheses
             GROUP BY session_id"""
    ) as cur:
        for row in await cur.fetchall():
            if row["session_id"] not in session_id_set:
                continue
            out[row["session_id"]].update(
                hypotheses=int(row["hypotheses"] or 0),
                reviewed=int(row["reviewed"] or 0),
            )

    async with conn.execute(
        """SELECT session_id,
                   SUM(CASE WHEN mode != 'invalid' THEN 1 ELSE 0 END) AS matches
              FROM tournament_matches
             GROUP BY session_id"""
    ) as cur:
        for row in await cur.fetchall():
            if row["session_id"] in session_id_set:
                out[row["session_id"]]["matches"] = int(row["matches"] or 0)

    async with conn.execute(
        """SELECT session_id, COUNT(*) AS assays
              FROM assay_proposals
             GROUP BY session_id"""
    ) as cur:
        for row in await cur.fetchall():
            if row["session_id"] in session_id_set:
                out[row["session_id"]]["assays"] = int(row["assays"] or 0)

    async with conn.execute(
        """SELECT session_id, COUNT(*) AS candidates
              FROM therapeutic_candidates
             GROUP BY session_id"""
    ) as cur:
        for row in await cur.fetchall():
            if row["session_id"] in session_id_set:
                out[row["session_id"]]["candidates"] = int(row["candidates"] or 0)

    return out


async def _current_tasks(conn: aiosqlite.Connection, session_id: str) -> list[dict[str, Any]]:
    async with conn.execute(
        """SELECT id, agent, action, status, attempts, started_at, last_error
             FROM tasks
            WHERE session_id=? AND status IN ('leased', 'in_progress')
            ORDER BY started_at DESC, created_at DESC
            LIMIT 5""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "id": row["id"],
            "agent": row["agent"],
            "action": row["action"],
            "status": row["status"],
            "attempts": row["attempts"],
            "started_at": row["started_at"],
            "last_error": row["last_error"],
        }
        for row in rows
    ]


async def _task_attempts(conn: aiosqlite.Connection, session_id: str) -> int:
    async with conn.execute(
        "SELECT COALESCE(SUM(attempts), 0) AS attempts FROM tasks WHERE session_id=?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    return int(row["attempts"] or 0) if row is not None else 0


async def _task_phase_counts(
    conn: aiosqlite.Connection, session_id: str
) -> dict[str, dict[str, int]]:
    async with conn.execute(
        """SELECT agent, action, status, COUNT(*) AS n
             FROM tasks
            WHERE session_id=?
            GROUP BY agent, action, status""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        phase = _task_phase(row["agent"], row["action"])
        if phase is None:
            continue
        phase_counts = out.setdefault(phase, {})
        phase_counts[row["status"]] = phase_counts.get(row["status"], 0) + int(row["n"])
    return out


async def _latest_activity_at(
    conn: aiosqlite.Connection, session: Session
) -> datetime | None:
    candidates = [session.updated_at]
    async with conn.execute(
        """SELECT
               (SELECT MAX(created_at) FROM tasks WHERE session_id=?) AS task_created_at,
               (SELECT MAX(started_at) FROM tasks WHERE session_id=?) AS task_started_at,
               (SELECT MAX(finished_at) FROM tasks WHERE session_id=?) AS task_finished_at,
               (SELECT MAX(started_at) FROM transcripts WHERE session_id=?) AS transcript_started_at,
               (SELECT MAX(finished_at) FROM transcripts WHERE session_id=?) AS transcript_finished_at,
               (SELECT MAX(ts) FROM events WHERE session_id=?) AS event_ts""",
        (session.id, session.id, session.id, session.id, session.id, session.id),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        for key in (
            "task_created_at",
            "task_started_at",
            "task_finished_at",
            "transcript_started_at",
            "transcript_finished_at",
        ):
            if row[key]:
                candidates.append(datetime.fromisoformat(row[key]))
        if row["event_ts"]:
            candidates.append(datetime.fromtimestamp(row["event_ts"] / 1000, UTC))
    return max(candidates, key=lambda dt: _as_utc(dt).timestamp()) if candidates else None


def _evidence_artifacts(cfg: Config, session_id: str) -> list[WorkspaceArtifact]:
    workspace = ScientistWorkspace(cfg, session_id)
    if not workspace.manifest_path.is_file():
        return []
    try:
        raw = json.loads(workspace.manifest_path.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    artifacts: list[WorkspaceArtifact] = []
    for item in raw:
        try:
            artifacts.append(WorkspaceArtifact.model_validate(item))
        except (TypeError, ValueError, ValidationError):
            continue
    evidence_kinds = {"evidence_bundle", "retrieved_literature", "project_file"}
    return [artifact for artifact in artifacts if artifact.kind in evidence_kinds]


async def _count(conn: aiosqlite.Connection, table: str, session_id: str) -> int:
    if table not in {
        "reviews",
        "assay_evaluations",
        "therapeutic_candidate_evaluations",
        "analysis_runs",
        "experiment_insights",
    }:
        raise ValueError(f"unsupported dashboard count table: {table}")
    async with conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE session_id=?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    return int(row["n"] or 0) if row is not None else 0


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        status=row["status"],
        workflow=row["workflow"],
        research_goal=row["research_goal"],
        research_plan=ResearchPlan.model_validate_json(row["research_plan"]),
        config_snapshot=json.loads(row["config_snapshot"]),
        budget_tokens=row["budget_tokens"],
        budget_usd=row["budget_usd"],
        budget_used_tokens=row["budget_used_tokens"],
        budget_used_usd=row["budget_used_usd"],
        wall_deadline=datetime.fromisoformat(row["wall_deadline"]) if row["wall_deadline"] else None,
        final_overview=row["final_overview"],
    )


def _attention_level(status: str, task_counts: dict[str, int]) -> str:
    if task_counts.get("dead", 0) or status in {"failed", "aborted"}:
        return "danger"
    if task_counts.get("failed", 0) or status == "paused":
        return "warning"
    if status == "done":
        return "complete"
    return "active" if status in ACTIVE_STATUSES else "neutral"


def _index_scientific_summary(workflow: str, counts: dict[str, int]) -> str:
    if workflow == "therapeutic_discovery":
        return f"{counts.get('assays', 0)} assays, {counts.get('candidates', 0)} candidates"
    return f"{counts.get('hypotheses', 0)} hypotheses, {counts.get('matches', 0)} matches"


def _task_phase(agent: str, action: str) -> str | None:
    if action == "GenerateFinalResearchOverview":
        return "outputs"
    if agent == "generation":
        return "generation"
    if agent == "reflection":
        return "review"
    if agent == "ranking":
        return "tournament"
    if agent in {"evolution", "proximity"}:
        return "evolution"
    if agent == "assay":
        return "assays"
    if agent == "candidate":
        return "candidates"
    if agent in {"analysis", "result_interpreter"}:
        return "analysis"
    if agent == "metareview":
        return "outputs"
    return None


def _phase_status_count(
    phase_task_counts: dict[str, dict[str, int]], phase: str, status: str
) -> int:
    return phase_task_counts.get(phase, {}).get(status, 0)


def _task_statuses(task_counts: dict[str, int]) -> list[str]:
    ordered = ["pending", "leased", "in_progress", "done", "failed", "dead", "cancelled"]
    return ordered + sorted(status for status in task_counts if status not in ordered)


def _state_from_count(count: int) -> str:
    return "ready" if count > 0 else "waiting"


def _short_id(session_id: str) -> str:
    return session_id[-6:] if len(session_id) > 6 else session_id


def _dashboard_path(session_id: str) -> str:
    return f"/sessions/{session_id}/dashboard"


def _overview_path(session: Session) -> str | None:
    return f"/sessions/{session.id}/overview" if session.final_overview else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
