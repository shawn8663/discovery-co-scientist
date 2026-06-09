"""Tests for CLI dashboard link helpers."""

from __future__ import annotations

from pathlib import Path

from co_scientist import cli
from co_scientist.cli import _dashboard_base_url, _dashboard_links, _print_dashboard_links
from co_scientist.config import Config, WebUICfg


def test_dashboard_base_url_uses_localhost_for_loopback() -> None:
    cfg = Config(web_ui=WebUICfg(host="127.0.0.1", port=7878))

    assert _dashboard_base_url(cfg) == "http://localhost:7878"


def test_dashboard_base_url_uses_localhost_for_wildcard_bind_host() -> None:
    cfg = Config(web_ui=WebUICfg(host="0.0.0.0", port=9000))

    assert _dashboard_base_url(cfg) == "http://localhost:9000"


def test_dashboard_base_url_preserves_non_loopback_host() -> None:
    cfg = Config(web_ui=WebUICfg(host="dashboard.local", port=9000))

    assert _dashboard_base_url(cfg) == "http://dashboard.local:9000"


def test_dashboard_links_include_runs_and_session_url() -> None:
    cfg = Config(web_ui=WebUICfg(host="127.0.0.1", port=7878))

    links = _dashboard_links(cfg, "ses_cli_dash")

    assert links["runs"] == "http://localhost:7878/runs"
    assert links["session"] == "http://localhost:7878/sessions/ses_cli_dash/dashboard"
    assert links["serve_command"] == "discovery-coscientist serve"


def test_dashboard_links_include_config_file_in_serve_command() -> None:
    cfg = Config(web_ui=WebUICfg(host="127.0.0.1", port=7878))
    object.__setattr__(cfg, "_cli_config_file", Path("/tmp/custom config.toml"))

    links = _dashboard_links(cfg, "ses_cli_dash")

    assert links["serve_command"] == "discovery-coscientist --config '/tmp/custom config.toml' serve"


def test_print_dashboard_links_can_emit_only_session_link(monkeypatch) -> None:
    cfg = Config(web_ui=WebUICfg(host="127.0.0.1", port=7878))
    lines: list[str] = []

    class FakeConsole:
        def print(self, message: str) -> None:
            lines.append(message)

    monkeypatch.setattr(cli, "console", FakeConsole())

    _print_dashboard_links(
        cfg,
        "ses_cli_dash",
        include_runs=False,
        include_serve_command=False,
    )

    assert lines == ["This run:       http://localhost:7878/sessions/ses_cli_dash/dashboard"]
