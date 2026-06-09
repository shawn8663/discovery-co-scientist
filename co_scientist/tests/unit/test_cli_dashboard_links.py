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
