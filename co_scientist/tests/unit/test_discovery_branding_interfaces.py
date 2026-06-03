"""Public identity and workflow interface tests for Discovery Co-Scientist."""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path

from co_scientist import cli
from co_scientist.agents.supervisor import Supervisor


def test_pyproject_renames_distribution_and_keeps_cli_aliases() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text())

    assert data["project"]["name"] == "discovery-co-scientist"
    assert data["project"]["scripts"]["discovery-coscientist"] == "co_scientist.cli:app"
    assert data["project"]["scripts"]["co-scientist"] == "co_scientist.cli:app"


def test_version_reports_new_public_identity() -> None:
    assert cli.PRIMARY_CLI == "discovery-coscientist"
    assert cli.PRODUCT_NAME == "Discovery Co-Scientist"
    assert cli.VERSION


def test_run_command_exposes_workflow_and_disease_options() -> None:
    sig = inspect.signature(cli.run)

    assert "workflow" in sig.parameters
    assert "disease" in sig.parameters
    assert sig.parameters["workflow"].default.default == "general_hypothesis"


def test_analyze_command_exposes_dataset_and_kind_options() -> None:
    sig = inspect.signature(cli.analyze)

    assert "kind" in sig.parameters
    assert "dataset" in sig.parameters


def test_supervisor_run_session_accepts_workflow_parameter() -> None:
    sig = inspect.signature(Supervisor.run_session)

    assert "workflow" in sig.parameters


def test_new_session_form_contains_workflow_selector() -> None:
    html = Path("co_scientist/web/templates/new_session.html").read_text(encoding="utf-8")

    assert 'name="workflow"' in html
    assert 'value="general_hypothesis"' in html
    assert 'value="therapeutic_discovery"' in html
