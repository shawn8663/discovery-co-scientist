"""FastAPI web UI for the co-scientist.

One process per host: launching `co-scientist serve` runs both the API + UI
and the worker pool — the queue is DB-backed so CLI `co-scientist run` in a
separate terminal feeds tasks to whatever Supervisor is currently active.

The UI is server-side Jinja2 + htmx for partial updates + SSE for live events.
No JS build step. Pico.css for default styling.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging as stdlib_logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from .. import ids
from ..config import Config, load_config
from ..logging import get_logger
from ..models import SystemFeedback
from ..orchestrator.events import GLOBAL_BUS
from ..storage import db as db_mod
from ..storage.artifacts import read_json
from ..storage.repos import events as events_repo
from ..storage.repos import feedback as fb_repo
from ..storage.repos import hypotheses as hyp_repo
from ..storage.repos import reviews as rev_repo
from ..storage.repos import robin as robin_repo
from ..storage.repos import sessions as sess_repo
from ..storage.repos import transcripts as tx_repo
from ..tools.local_pdf_search import _looks_like_pdf, _read_or_index_pdf
from ..workspace import ScientistWorkspace
from .dashboard import (
    runs_index as build_runs_index,
    session_dashboard as build_session_dashboard,
)
from .sanitize import render_markdown

log = get_logger("web")
HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=HERE / "templates")
UPLOAD_FILE_PARAM = File(...)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="AI Co-Scientist")
    app.state.cfg = cfg
    app.state.background_runs: dict[str, asyncio.Task] = {}

    # Static
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    # ----------------------------- pages ----------------------------- #

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

    @app.get("/sessions/new", response_class=HTMLResponse)
    async def new_session_form(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request, "new_session.html", {"default_budget": cfg.run.budget_usd}
        )

    @app.post("/sessions/new")
    async def create_session(
        request: Request,
        background_tasks: BackgroundTasks,
        goal: str = Form(...),
        workflow: str = Form("general_hypothesis"),
        disease: str = Form(""),
        preferences: str = Form(""),
        project_paths: str = Form(""),
        science_skills_path: str = Form(""),
        budget_usd: float = Form(cfg.run.budget_usd),
        n_initial: int = Form(3),
        wall_clock_seconds: int = Form(cfg.run.wall_clock_seconds),
    ) -> RedirectResponse:
        from ..agents.supervisor import Supervisor
        from ..workspace.ingest import collect_project_files

        # Hand the Supervisor a fresh Config copy so per-session knobs don't leak.
        sup_cfg = cfg.model_copy(deep=True)
        sup_cfg.run.budget_usd = budget_usd
        sup_cfg.run.wall_clock_seconds = wall_clock_seconds
        if science_skills_path.strip():
            sup_cfg.science_skills.path = science_skills_path.strip()
        sup = Supervisor(sup_cfg)
        files, dirs = _parse_project_paths(project_paths)
        initial_project_files = collect_project_files(files=files, dirs=dirs)
        effective_goal = goal.strip()
        if workflow == "therapeutic_discovery" and disease.strip() and not effective_goal:
            effective_goal = f"Discover therapeutics for {disease.strip()}"

        async def _run() -> None:
            try:
                await sup.run_session(
                    goal=effective_goal,
                    preferences_text=preferences or None,
                    project_files=initial_project_files,
                    n_initial=n_initial,
                    wall_clock_seconds=wall_clock_seconds,
                    workflow=workflow,  # type: ignore[arg-type]
                )
            except Exception:
                log.exception("background_run_failed")

        task = asyncio.create_task(_run())
        # No durable session id at this point — give the run a chance to insert
        # the row, then redirect to /. The user can find it in the listing.
        _ = task
        return RedirectResponse(url="/", status_code=303)

    @app.get("/sessions/{session_id}", response_class=HTMLResponse)
    async def session_detail(request: Request, session_id: str) -> HTMLResponse:
        conn = await db_mod.connect(cfg)
        try:
            session = await sess_repo.fetch(conn, session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="session not found")
            hyps = await hyp_repo.list_for_session(conn, session_id)
            recent_matches = await _recent_matches(conn, session_id, limit=20)
            usage = await tx_repo.usage_summary(conn, session_id)
            artifacts = ScientistWorkspace(cfg, session_id).list()
            robin_context = {}
            if session.workflow == "therapeutic_discovery":
                robin_context = {
                    "assays": await robin_repo.list_assays(conn, session_id),
                    "candidates": await robin_repo.list_candidates(conn, session_id),
                    "analysis_runs": await robin_repo.list_analysis_runs(conn, session_id),
                    "experiment_insights": await robin_repo.list_experiment_insights(conn, session_id),
                }
            return TEMPLATES.TemplateResponse(
                request,
                "session_detail.html",
                {
                    "session": session,
                    "hypotheses": sorted(hyps, key=lambda h: -(h.elo or 0)),
                    "recent_matches": recent_matches,
                    "usage": usage,
                    "workspace_artifacts": artifacts,
                    **robin_context,
                },
            )
        finally:
            await conn.close()

    @app.get("/sessions/{session_id}/dashboard", response_class=HTMLResponse)
    async def session_dashboard_page(request: Request, session_id: str) -> HTMLResponse:
        conn = await db_mod.connect(cfg)
        try:
            try:
                dashboard = await build_session_dashboard(cfg, conn, session_id)
            except KeyError as e:
                raise HTTPException(status_code=404, detail="session not found")
            return TEMPLATES.TemplateResponse(
                request,
                "session_dashboard.html",
                {"dashboard": dashboard},
            )
        finally:
            await conn.close()

    @app.get("/sessions/{session_id}/hypotheses/{hid}", response_class=HTMLResponse)
    async def hypothesis_detail(request: Request, session_id: str, hid: str) -> HTMLResponse:
        conn = await db_mod.connect(cfg)
        try:
            h = await hyp_repo.fetch(conn, hid)
            session = await sess_repo.fetch(conn, session_id)
            if h is None or session is None:
                raise HTTPException(status_code=404, detail="not found")
            reviews = await rev_repo.list_for_hypothesis(conn, hid)
            return TEMPLATES.TemplateResponse(
                request,
                "hypothesis_detail.html",
                {
                    "session": session,
                    "h": h,
                    "reviews": reviews,
                    "full_text_html": render_markdown(h.full_text or ""),
                },
            )
        finally:
            await conn.close()

    @app.get("/sessions/{session_id}/overview", response_class=HTMLResponse)
    async def session_overview(request: Request, session_id: str) -> HTMLResponse:
        conn = await db_mod.connect(cfg)
        try:
            session = await sess_repo.fetch(conn, session_id)
            if session is None or not session.final_overview:
                raise HTTPException(
                    status_code=404, detail="no final overview yet for this session"
                )
            # `final_overview` is written by the supervisor under
            # `data_dir/artifacts/...` but is stored as a string in the DB.
            # Resolve and confirm the path is still inside `data_dir` so a
            # tampered row can't read arbitrary files.
            base = cfg.data_dir.resolve()
            try:
                path = (cfg.data_dir / session.final_overview).resolve()
                path.relative_to(base)
            except (ValueError, OSError) as e:
                log.error("overview_path_escape", session=session_id, err=str(e))
                raise HTTPException(status_code=404, detail="overview unavailable") from e
            if not path.is_file():
                raise HTTPException(status_code=404, detail="overview missing on disk")
            overview_md = path.read_text()
            safety_context = _overview_safety_context(cfg, session.final_overview, overview_md)
            return TEMPLATES.TemplateResponse(
                request,
                "overview.html",
                {
                    "session": session,
                    "overview_html": render_markdown(overview_md),
                    "overview_md": overview_md,
                    **safety_context,
                },
            )
        finally:
            await conn.close()

    @app.get("/sessions/{session_id}/artifact")
    async def session_artifact(session_id: str, path: str):
        prefix = f"artifacts/{session_id}/"
        if not path.startswith(prefix):
            raise HTTPException(status_code=404, detail="artifact unavailable")
        base = cfg.data_dir.resolve()
        try:
            resolved = (cfg.data_dir / path).resolve()
            resolved.relative_to(base)
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=404, detail="artifact unavailable") from e
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="artifact missing")
        if resolved.suffix == ".json":
            return JSONResponse(await read_json(cfg, path))
        return PlainTextResponse(resolved.read_text())

    # ----------------------------- API + SSE ----------------------------- #

    @app.get("/api/sessions/{session_id}/metrics")
    async def api_metrics(session_id: str) -> JSONResponse:
        from ..obs.metrics import session_metrics_cached, to_dict

        conn = await db_mod.connect(cfg)
        try:
            m = await session_metrics_cached(conn, session_id)
            return JSONResponse(to_dict(m))
        finally:
            await conn.close()

    @app.get("/api/sessions/{session_id}")
    async def api_session(session_id: str) -> JSONResponse:
        conn = await db_mod.connect(cfg)
        try:
            s = await sess_repo.fetch(conn, session_id)
            if s is None:
                raise HTTPException(status_code=404)
            return JSONResponse(s.model_dump(mode="json"))
        finally:
            await conn.close()

    @app.get("/api/sessions/{session_id}/events")
    async def api_events(session_id: str) -> EventSourceResponse:
        async def _stream() -> AsyncIterator[dict[str, Any]]:
            # Replay last 25 events from DB so refreshes don't go blank.
            conn = await db_mod.connect(cfg)
            try:
                history = await events_repo.recent(conn, session_id, limit=25)
            finally:
                await conn.close()
            for ev in reversed(history):
                yield {
                    "event": ev["event"],
                    "data": json.dumps({"payload": ev["payload"], "ts": ev["ts"]}),
                }
            async with contextlib.aclosing(GLOBAL_BUS.subscribe(session_id)) as gen:
                async for ev in gen:
                    yield {
                        "event": ev.name,
                        "data": ev.to_json(),
                    }

        return EventSourceResponse(_stream())

    @app.post("/api/sessions/{session_id}/pause")
    async def api_pause(session_id: str) -> JSONResponse:
        conn = await db_mod.connect(cfg)
        try:
            await sess_repo.set_status(conn, session_id, "paused")
            await GLOBAL_BUS.publish(session_id, "session_paused", {})
            return JSONResponse({"ok": True})
        finally:
            await conn.close()

    @app.post("/api/sessions/{session_id}/resume")
    async def api_resume(session_id: str) -> JSONResponse:
        conn = await db_mod.connect(cfg)
        try:
            await sess_repo.set_status(conn, session_id, "running")
            await GLOBAL_BUS.publish(session_id, "session_resumed", {})
            return JSONResponse({"ok": True})
        finally:
            await conn.close()

    @app.post("/api/sessions/{session_id}/abort")
    async def api_abort(session_id: str) -> JSONResponse:
        conn = await db_mod.connect(cfg)
        try:
            await sess_repo.set_status(conn, session_id, "aborted")
            await GLOBAL_BUS.publish(session_id, "session_aborted", {})
            return JSONResponse({"ok": True})
        finally:
            await conn.close()

    @app.post("/api/sessions/{session_id}/feedback")
    async def api_feedback(
        session_id: str,
        text: str = Form(...),
        kind: str = Form("directive"),
        target_type: str = Form("hypothesis"),
        target_id: str = Form(""),
    ) -> JSONResponse:
        conn = await db_mod.connect(cfg)
        try:
            from ..storage.repos import robin as robin_repo

            fb = SystemFeedback(
                id=ids.feedback_id(), session_id=session_id,
                created_at=datetime.now(UTC),
                source="human", kind=kind,
                target_id=target_id or None, text=text, active=True,
            )
            await fb_repo.insert(conn, fb)
            if target_id:
                state = "pinned" if kind == "pin" else "rejected" if kind == "rejection" else None
                if state and target_type == "assay":
                    await robin_repo.set_assay_state(conn, target_id, state)
                elif state and target_type == "candidate":
                    await robin_repo.set_candidate_state(conn, target_id, state)
                elif state:
                    await hyp_repo.set_state(conn, target_id, state)
            await GLOBAL_BUS.publish(session_id, "human_feedback", {
                "kind": kind,
                "target_type": target_type,
                "target_id": target_id or None,
                "text": text[:200],
            })
            return JSONResponse({"ok": True, "feedback_id": fb.id})
        finally:
            await conn.close()

    @app.post("/api/sessions/{session_id}/workspace/upload")
    async def api_workspace_upload(
        session_id: str,
        file: UploadFile = UPLOAD_FILE_PARAM,
    ) -> JSONResponse:
        conn = await db_mod.connect(cfg)
        try:
            session = await sess_repo.fetch(conn, session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="session not found")
        finally:
            await conn.close()

        workspace = ScientistWorkspace(cfg, session_id)
        workspace.ensure()
        filename = Path(file.filename or "upload.bin").name
        dest = workspace.root / "uploads" / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = await file.read()
        dest.write_bytes(data)
        artifact = workspace.add_artifact(
            kind="project_file",
            path=dest,
            title=filename,
            provenance={"source": "web_upload"},
            metadata={
                "content_type": file.content_type or "application/octet-stream",
                "size_bytes": len(data),
            },
        )
        indexed = False
        parse_error = ""
        if _looks_like_pdf(artifact):
            try:
                _read_or_index_pdf(cfg, artifact, dest)
                indexed = True
            except Exception as e:
                parse_error = str(e)
        return JSONResponse({
            "artifact": artifact.model_dump(mode="json"),
            "indexed": indexed,
            "parse_error": parse_error,
        })

    @app.get("/healthz")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True})

    # quiet uvicorn access spam during streaming
    stdlib_logging.getLogger("uvicorn.access").setLevel(stdlib_logging.WARNING)
    return app


# ----------------------------- helpers ----------------------------- #


def _overview_safety_context(
    cfg: Config,
    overview_rel_path: str,
    overview_md: str,
) -> dict[str, Any]:
    stripped = overview_md.lstrip()
    if stripped.startswith("# Research overview withheld"):
        status = "withheld"
        title = "Final overview withheld"
        message = "The generated overview was blocked or quarantined by the final safety gate."
    elif stripped.startswith("> Safety review warning:"):
        status = "warning"
        title = "Safety review warning"
        message = "The generated overview passed with a safety warning."
    else:
        return {
            "safety_status": None,
            "safety_title": "",
            "safety_message": "",
            "safety_artifact_url": "",
        }

    safety_rel_path = str(Path(overview_rel_path).with_name("overview_safety.json"))
    safety_path = cfg.data_dir / safety_rel_path
    safety_artifact_url = ""
    if safety_path.is_file():
        session_id = Path(overview_rel_path).parts[1] if len(Path(overview_rel_path).parts) > 1 else ""
        safety_artifact_url = f"/sessions/{session_id}/artifact?path={safety_rel_path}"
    return {
        "safety_status": status,
        "safety_title": title,
        "safety_message": message,
        "safety_artifact_url": safety_artifact_url,
    }


def _parse_project_paths(project_paths: str) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    dirs: list[Path] = []
    for raw in project_paths.splitlines():
        value = raw.strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_dir():
            dirs.append(path)
        else:
            files.append(path)
    return files, dirs


async def _list_sessions(cfg: Config) -> list[dict[str, Any]]:
    conn = await db_mod.connect(cfg)
    try:
        async with conn.execute(
            """SELECT id, status, research_goal, created_at, updated_at,
                      budget_usd, budget_used_usd,
                      (SELECT COUNT(*) FROM hypotheses WHERE session_id = s.id) AS n_hyps,
                      (SELECT MAX(elo) FROM hypotheses WHERE session_id = s.id) AS top_elo
                 FROM sessions s
                 ORDER BY updated_at DESC LIMIT 50""",
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _recent_matches(conn, session_id: str, *, limit: int) -> list[dict[str, Any]]:
    async with conn.execute(
        """SELECT id, hyp_a, hyp_b, mode, winner, elo_a_after, elo_b_after, created_at
              FROM tournament_matches
             WHERE session_id=?
             ORDER BY created_at DESC LIMIT ?""",
        (session_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
