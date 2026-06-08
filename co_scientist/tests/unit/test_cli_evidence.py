"""CLI evidence-bundle preview command."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from typer.testing import CliRunner

from co_scientist.cli import app
from co_scientist.config import load_config
from co_scientist.logging import setup_logging
from co_scientist.storage import db as db_mod
from co_scientist.workspace import ScientistWorkspace


def test_evidence_command_builds_bundle_without_enqueuing_tasks(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    data_dir = tmp_path / "data"
    cfg_file.write_text(f'[storage]\ndata_dir = "{data_dir}"\n')
    background = tmp_path / "background.txt"
    background.write_text("Background DOI 10.1234/example. PMID: 12345678")

    try:
        result = CliRunner().invoke(
            app,
            [
                "--config",
                str(cfg_file),
                "evidence",
                "--no-parse-goal",
                "--project-file",
                str(background),
                "Find mechanisms for microtubule inhibition in cancer",
            ],
        )
    finally:
        structlog.reset_defaults()
        setup_logging()

    assert result.exit_code == 0, result.output
    assert "Evidence bundle created" in result.output
    assert "No generation tasks were enqueued" in result.output
    session_id = _extract_session_id(result.output)

    cfg = load_config(cfg_file)

    async def _counts() -> tuple[int, dict]:
        conn = await db_mod.connect(cfg)
        try:
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE session_id=?",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()
            async with conn.execute(
                "SELECT research_plan FROM sessions WHERE id=?",
                (session_id,),
            ) as cur:
                session_row = await cur.fetchone()
            return int(row["n"]), json.loads(session_row["research_plan"])
        finally:
            await conn.close()

    n_tasks, plan = asyncio.run(_counts())
    assert n_tasks == 0
    assert plan["retrieval_queries"] == ["Find mechanisms for microtubule inhibition in cancer"]
    artifacts = ScientistWorkspace(cfg, session_id).list()
    assert [artifact.kind for artifact in artifacts] == ["project_file", "evidence_bundle"]


def _extract_session_id(output: str) -> str:
    for token in output.replace("\n", " ").split():
        if token.startswith("session="):
            return token.split("=", 1)[1]
    raise AssertionError(f"missing session id in output:\n{output}")
