"""Runbook parsing and CLI planning."""

from __future__ import annotations

from pathlib import Path

import structlog
from typer.testing import CliRunner

from co_scientist.cli import app
from co_scientist.logging import setup_logging
from co_scientist.runbook import load_runbook


def test_load_runbook_requires_approval(tmp_path: Path) -> None:
    path = tmp_path / "workflow.toml"
    path.write_text(
        """
title = "Unapproved workflow"

[[steps]]
name = "first"
prompt = "Do the first run"
"""
    )

    try:
        load_runbook(path)
    except ValueError as exc:
        assert "approved = true" in str(exc)
    else:
        raise AssertionError("unapproved runbook should be rejected")


def test_load_runbook_inherits_defaults_and_reads_prompt_file(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Develop hypotheses about surface markers.")
    prefs = tmp_path / "prefs.txt"
    prefs.write_text("Prefer experimentally tractable ideas.")
    project = tmp_path / "background.pdf"
    project.write_text("not really a pdf")
    path = tmp_path / "workflow.toml"
    path.write_text(
        f"""
title = "Approved workflow"
approved = true

[defaults]
workflow = "general_hypothesis"
n = 12
wall_clock = 3600
budget_usd = 40
preferences_file = "{prefs.name}"
project_file = ["{project.name}"]

[[steps]]
name = "surfaceome"
prompt_file = "{prompt.name}"
"""
    )

    runbook = load_runbook(path)

    assert runbook.title == "Approved workflow"
    assert len(runbook.steps) == 1
    step = runbook.steps[0]
    assert step.name == "surfaceome"
    assert step.prompt == "Develop hypotheses about surface markers."
    assert step.workflow == "general_hypothesis"
    assert step.n == 12
    assert step.wall_clock == 3600
    assert step.budget_usd == 40
    assert step.preferences_file == prefs
    assert step.project_files == [project]


def test_runbook_dry_run_lists_approved_steps(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    data_dir = tmp_path / "data"
    cfg_file.write_text(f'[storage]\ndata_dir = "{data_dir}"\n')
    path = tmp_path / "workflow.toml"
    path.write_text(
        """
title = "Approved workflow"
approved = true

[defaults]
n = 5
budget_usd = 20

[[steps]]
name = "first"
prompt = "Run first prompt"

[[steps]]
name = "second"
prompt = "Run second prompt"
n = 8
"""
    )

    try:
        result = CliRunner().invoke(
            app,
            ["--config", str(cfg_file), "runbook", "execute", str(path), "--dry-run"],
        )
    finally:
        structlog.reset_defaults()
        setup_logging()

    assert result.exit_code == 0, result.output
    assert "Runbook: Approved workflow" in result.output
    assert "DRY RUN" in result.output
    assert "1. first" in result.output
    assert "n=5" in result.output
    assert "2. second" in result.output
    assert "n=8" in result.output


def test_runbook_execute_runs_steps_in_order_and_writes_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg_file = tmp_path / "config.toml"
    data_dir = tmp_path / "data"
    cfg_file.write_text(
        f"""
[storage]
data_dir = "{data_dir}"

[llm]
provider = "ollama"
"""
    )
    summary = tmp_path / "summary.md"
    path = tmp_path / "workflow.toml"
    path.write_text(
        f"""
title = "Approved workflow"
approved = true

[summary]
output = "{summary.name}"

[defaults]
n = 5
budget_usd = 20
wall_clock = 900

[[steps]]
name = "first"
prompt = "Run first prompt"

[[steps]]
name = "second"
prompt = "Run second prompt"
n = 8
"""
    )
    calls: list[dict] = []

    async def fake_run_session(self, goal, **kwargs):
        calls.append({"goal": goal, **kwargs})
        return f"ses_fake_{len(calls)}"

    monkeypatch.setattr(
        "co_scientist.agents.supervisor.Supervisor.run_session",
        fake_run_session,
    )

    try:
        result = CliRunner().invoke(
            app,
            ["--config", str(cfg_file), "runbook", "execute", str(path)],
        )
    finally:
        structlog.reset_defaults()
        setup_logging()

    assert result.exit_code == 0, result.output
    assert [call["goal"] for call in calls] == ["Run first prompt", "Run second prompt"]
    assert [call["n_initial"] for call in calls] == [5, 8]
    assert all(call["wall_clock_seconds"] == 900 for call in calls)
    assert "ses_fake_1" in result.output
    assert "ses_fake_2" in result.output
    text = summary.read_text()
    assert "# Runbook Summary: Approved workflow" in text
    assert "ses_fake_1" in text
    assert "ses_fake_2" in text
