"""Tests for Paperclip retrieval integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from co_scientist.config import Config
from co_scientist.tools.base import ToolCtx
from co_scientist.tools.builtins.paperclip import (
    PaperclipLookupTool,
    PaperclipMapTool,
    PaperclipSearchTool,
)
from co_scientist.tools.cache import RetrievalCache
from co_scientist.tools.registry import ToolRegistry


@dataclass
class _FakeExecuteResult:
    output: str
    result_id: str
    raw: Any = None
    exit_code: int = 0
    elapsed_ms: int = 17


@dataclass
class _FakeMapEvent:
    type: str
    output: str = ""
    result_id: str = ""
    exit_code: int = 0
    elapsed_ms: int = 0
    completed: int = 0
    failed: int = 0
    total: int = 0


class _FakePaperclipClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search(self, query: str, **kwargs: Any) -> _FakeExecuteResult:
        self.calls.append(("search", {"query": query, **kwargs}))
        return _FakeExecuteResult(
            output="Found 1 paper",
            result_id="s_search123",
            raw={
                "results": [
                    {
                        "id": "PMC123",
                        "title": "Paperclip-enabled discovery",
                        "doi": "10.1000/paperclip",
                        "path": "/papers/PMC123",
                        "source": "pmc",
                        "year": 2026,
                    }
                ]
            },
        )

    def lookup(self, field: str, value: str, **kwargs: Any) -> _FakeExecuteResult:
        self.calls.append(("lookup", {"field": field, "value": value, **kwargs}))
        return _FakeExecuteResult(
            output="Found lookup",
            result_id="s_lookup123",
            raw={"results": [{"id": "PMC456", "title": "Lookup paper"}]},
        )

    def map_(self, question: str, **kwargs: Any) -> list[_FakeMapEvent]:
        self.calls.append(("map_", {"question": question, **kwargs}))
        return [
            _FakeMapEvent(type="progress", completed=1, total=1),
            _FakeMapEvent(
                type="result",
                output="Methods used lipid nanoparticles.",
                result_id="m_map123",
                exit_code=0,
                elapsed_ms=42,
            ),
        ]


def _enable_paperclip(cfg: Config) -> Config:
    cfg.paperclip.enabled = True
    cfg.secrets.PAPERCLIP_API_KEY = "gxl_fake"
    return cfg


def test_registry_registers_paperclip_tools_only_when_enabled(tmp_cfg) -> None:
    names = {tool.name for tool in ToolRegistry(tmp_cfg).discover().all()}
    assert "paperclip_search" not in names

    _enable_paperclip(tmp_cfg)
    names = {tool.name for tool in ToolRegistry(tmp_cfg).discover().all()}

    assert {"paperclip_search", "paperclip_lookup", "paperclip_map"}.issubset(names)


def test_generation_reflection_evolution_can_use_paperclip_tools_when_enabled(tmp_cfg) -> None:
    _enable_paperclip(tmp_cfg)
    reg = ToolRegistry(tmp_cfg).discover()

    for agent in ("generation", "reflection", "evolution"):
        names = {tool.name for tool in reg.tools_for(agent)}
        assert "paperclip_search" in names
        assert "paperclip_lookup" in names
        assert "paperclip_map" in names


async def test_paperclip_search_uses_sdk_and_retrieval_cache(tmp_cfg, monkeypatch) -> None:
    _enable_paperclip(tmp_cfg)
    client = _FakePaperclipClient()
    monkeypatch.setattr(
        "co_scientist.tools.builtins.paperclip._paperclip_client_from_env",
        lambda: client,
    )
    args = {"query": "CRISPR lipid nanoparticles", "max_results": 3, "source": "pmc"}

    result = await PaperclipSearchTool(tmp_cfg).call(args, ToolCtx(cfg=tmp_cfg, session_id="ses_pc"))

    assert result.is_error is False
    assert client.calls == [
        (
            "search",
            {
                "query": "CRISPR lipid nanoparticles",
                "limit": 3,
                "source": "pmc",
                "timeout": tmp_cfg.paperclip.timeout_seconds,
            },
        )
    ]
    assert result.content["result_id"] == "s_search123"
    assert result.content["results"][0]["doi"] == "10.1000/paperclip"
    assert result.metadata["retrieval_source"] == "paperclip_search"
    assert result.metadata["cache_hit"] is False

    client.calls.clear()
    cached = await PaperclipSearchTool(tmp_cfg).call(args, ToolCtx(cfg=tmp_cfg, session_id="ses_pc"))

    assert cached.is_error is False
    assert cached.content == result.content
    assert cached.metadata["cache_hit"] is True
    assert client.calls == []


async def test_paperclip_lookup_uses_sdk(tmp_cfg, monkeypatch) -> None:
    _enable_paperclip(tmp_cfg)
    client = _FakePaperclipClient()
    monkeypatch.setattr(
        "co_scientist.tools.builtins.paperclip._paperclip_client_from_env",
        lambda: client,
    )

    result = await PaperclipLookupTool(tmp_cfg).call(
        {"field": "doi", "value": "10.1000/paperclip"},
        ToolCtx(cfg=tmp_cfg, session_id="ses_pc"),
    )

    assert result.is_error is False
    assert result.content["result_id"] == "s_lookup123"
    assert client.calls[0][0] == "lookup"
    assert client.calls[0][1]["limit"] == tmp_cfg.paperclip.lookup_limit
    assert result.metadata["retrieval_source"] == "paperclip_lookup"


async def test_paperclip_map_uses_sdk_and_caches_result(tmp_cfg, monkeypatch) -> None:
    _enable_paperclip(tmp_cfg)
    client = _FakePaperclipClient()
    monkeypatch.setattr(
        "co_scientist.tools.builtins.paperclip._paperclip_client_from_env",
        lambda: client,
    )
    args = {"question": "What methods were used?", "from_results": "s_search123"}

    result = await PaperclipMapTool(tmp_cfg).call(args, ToolCtx(cfg=tmp_cfg, session_id="ses_pc"))

    assert result.is_error is False
    assert result.content["result_id"] == "m_map123"
    assert result.content["output"] == "Methods used lipid nanoparticles."
    assert result.content["progress"] == [{"completed": 1, "failed": 0, "total": 1}]
    assert result.metadata["retrieval_source"] == "paperclip_map"
    assert result.metadata["cache_hit"] is False

    cached_payload = RetrievalCache(tmp_cfg, "ses_pc").read("paperclip_map", args)
    assert cached_payload == result.content


async def test_paperclip_tool_reports_missing_sdk(tmp_cfg, monkeypatch) -> None:
    _enable_paperclip(tmp_cfg)

    def _missing_client():
        raise ImportError("No module named gxl_paperclip")

    monkeypatch.setattr(
        "co_scientist.tools.builtins.paperclip._paperclip_client_from_env",
        _missing_client,
    )

    result = await PaperclipSearchTool(tmp_cfg).call(
        {"query": "CRISPR"},
        ToolCtx(cfg=tmp_cfg, session_id="ses_pc"),
    )

    assert result.is_error is True
    assert "install the paperclip extra" in (result.error_message or "")
