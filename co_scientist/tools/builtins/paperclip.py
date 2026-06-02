"""Paperclip literature, regulatory document, and clinical trial retrieval.

Paperclip is optional. When enabled, these tools use the `gxl_paperclip`
Python SDK and the same retrieval cache/artifact path as the other literature
tools.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from ...config import Config
from ..base import ToolCtx, ToolResult
from ..cache import RetrievalCache


class PaperclipSearchTool:
    name = "paperclip_search"
    description = (
        "Search Paperclip's corpus of full-text papers, preprints, OpenAlex abstracts, "
        "FDA/regulatory documents, and clinical trials. Returns Paperclip result IDs "
        "that can be passed to paperclip_map for deeper paper-by-paper analysis."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
            "source": {
                "type": "string",
                "description": (
                    "Optional source filter such as pmc, biorxiv, medrxiv, arxiv, "
                    "fda, trials, or a comma-separated list."
                ),
            },
            "since": {"type": "string", "description": "Optional window such as 30d, 6m, 1y."},
            "sort": {"type": "string", "enum": ["relevance", "date"]},
            "mode": {"type": "string", "enum": ["any", "all", "50%", "75%"]},
            "exact": {"type": "boolean"},
            "all": {
                "type": "boolean",
                "description": "Search the full corpus instead of the recency-weighted slice.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(is_error=True, error_message="empty query")
        kwargs = _search_kwargs(args, ctx.cfg)
        cache_args = {"query": query, **kwargs}
        cached = RetrievalCache(ctx.cfg, ctx.session_id).read(self.name, cache_args)
        if cached is not None:
            return _cached_result(self.name, cached, t0)
        result = await _run_paperclip_call(lambda client: client.search(query, **kwargs))
        if result.is_error:
            return result
        payload = _execute_payload(result.content, query=query)
        RetrievalCache(ctx.cfg, ctx.session_id).write(self.name, cache_args, payload)
        return _fresh_result(self.name, payload, t0)


class PaperclipLookupTool:
    name = "paperclip_lookup"
    description = (
        "Look up Paperclip records by metadata field such as doi, pmc, pmid, author, "
        "title, journal, year, or keywords. Use when a hypothesis or citation already "
        "contains a specific identifier."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "value": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": ["field", "value"],
    }

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        field = str(args.get("field") or "").strip()
        value = str(args.get("value") or "").strip()
        if not field or not value:
            return ToolResult(is_error=True, error_message="field and value are required")
        kwargs = {
            "limit": _bounded_int(args.get("max_results"), default=ctx.cfg.paperclip.lookup_limit),
            "timeout": ctx.cfg.paperclip.timeout_seconds,
        }
        cache_args = {"field": field, "value": value, **kwargs}
        cached = RetrievalCache(ctx.cfg, ctx.session_id).read(self.name, cache_args)
        if cached is not None:
            return _cached_result(self.name, cached, t0)
        result = await _run_paperclip_call(lambda client: client.lookup(field, value, **kwargs))
        if result.is_error:
            return result
        payload = _execute_payload(result.content, field=field, value=value)
        RetrievalCache(ctx.cfg, ctx.session_id).write(self.name, cache_args, payload)
        return _fresh_result(self.name, payload, t0)


class PaperclipMapTool:
    name = "paperclip_map"
    description = (
        "Run Paperclip's AI reader over a previous paperclip_search or paperclip_lookup "
        "result set. Use selectively for promising hypotheses because map calls are "
        "higher-latency and higher-cost than search."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "from_results": {
                "type": "string",
                "description": "Paperclip search/lookup result ID such as s_14bebc10.",
            },
        },
        "required": ["question", "from_results"],
    }

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        if not ctx.cfg.paperclip.map_enabled:
            return ToolResult(is_error=True, error_message="paperclip_map is disabled in config")
        question = str(args.get("question") or "").strip()
        from_results = str(args.get("from_results") or "").strip()
        if not question or not from_results:
            return ToolResult(is_error=True, error_message="question and from_results are required")
        cache_args = {"question": question, "from_results": from_results}
        cached = RetrievalCache(ctx.cfg, ctx.session_id).read(self.name, cache_args)
        if cached is not None:
            return _cached_result(self.name, cached, t0)
        result = await _run_paperclip_call(
            lambda client: list(
                client.map_(
                    question,
                    from_results=from_results,
                    timeout=ctx.cfg.paperclip.map_timeout_seconds,
                )
            )
        )
        if result.is_error:
            return result
        payload = _map_payload(result.content, question=question, from_results=from_results)
        RetrievalCache(ctx.cfg, ctx.session_id).write(self.name, cache_args, payload)
        out = _fresh_result(self.name, payload, t0)
        out.metadata["paperclip_source_result_id"] = from_results
        return out


def _paperclip_client_from_env() -> Any:
    try:
        from gxl_paperclip import PaperclipClient
    except ImportError as e:
        raise ImportError(
            "Paperclip SDK is not installed; install the paperclip extra with "
            "`uv sync --extra paperclip` or install `gxl-paperclip`."
        ) from e
    return PaperclipClient.from_env()


async def _run_paperclip_call(fn: Callable[[Any], Any]) -> ToolResult:
    def _call() -> Any:
        client = _paperclip_client_from_env()
        return fn(client)

    try:
        return ToolResult(content=await asyncio.to_thread(_call))
    except ImportError as e:
        message = str(e)
        if "install the paperclip extra" not in message:
            message = (
                f"{message}; install the paperclip extra with `uv sync --extra paperclip` "
                "or install `gxl-paperclip`."
            )
        return ToolResult(is_error=True, error_message=message)
    except Exception as e:
        return ToolResult(is_error=True, error_message=f"paperclip failed: {e}")


def _search_kwargs(args: dict[str, Any], cfg: Config) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "limit": _bounded_int(args.get("max_results"), default=cfg.paperclip.default_limit),
        "timeout": cfg.paperclip.timeout_seconds,
    }
    source = str(args.get("source") or cfg.paperclip.default_sources or "").strip()
    if source:
        kwargs["source"] = source
    for key in ("since", "sort", "mode", "author", "journal", "year", "type", "category"):
        value = args.get(key)
        if value not in (None, ""):
            kwargs[key] = value
    for key in ("exact", "all"):
        if bool(args.get(key)):
            kwargs[key] = True
    return kwargs


def _bounded_int(value: Any, *, default: int, maximum: int = 1000) -> int:
    try:
        n = int(value or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(maximum, n))


def _execute_payload(result: Any, **context: Any) -> dict[str, Any]:
    raw = getattr(result, "raw", None)
    records = _records_from_raw(raw)
    payload = {
        **context,
        "result_id": getattr(result, "result_id", None),
        "output": getattr(result, "output", ""),
        "n": len(records),
        "results": records,
    }
    elapsed_ms = getattr(result, "elapsed_ms", None)
    if elapsed_ms is not None:
        payload["elapsed_ms"] = elapsed_ms
    exit_code = getattr(result, "exit_code", None)
    if exit_code is not None:
        payload["exit_code"] = exit_code
    return payload


def _records_from_raw(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        for key in ("results", "rows", "documents", "items"):
            records = raw.get(key)
            if isinstance(records, list):
                return [_record_dict(record) for record in records]
        return [_record_dict(raw)] if raw else []
    if isinstance(raw, list):
        return [_record_dict(record) for record in raw]
    return []


def _record_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return {
            key: value
            for key, value in record.items()
            if value not in (None, "", [], {})
        }
    return {"value": str(record)}


def _map_payload(events: Any, *, question: str, from_results: str) -> dict[str, Any]:
    progress: list[dict[str, int]] = []
    result_event: Any | None = None
    for event in events if isinstance(events, list) else list(events):
        if getattr(event, "type", "") == "progress":
            progress.append(
                {
                    "completed": int(getattr(event, "completed", 0) or 0),
                    "failed": int(getattr(event, "failed", 0) or 0),
                    "total": int(getattr(event, "total", 0) or 0),
                }
            )
        else:
            result_event = event
    return {
        "question": question,
        "from_results": from_results,
        "result_id": getattr(result_event, "result_id", None),
        "output": getattr(result_event, "output", ""),
        "progress": progress,
        "exit_code": getattr(result_event, "exit_code", None),
        "elapsed_ms": getattr(result_event, "elapsed_ms", None),
    }


def _cached_result(tool_name: str, payload: dict[str, Any], t0: float) -> ToolResult:
    return ToolResult(
        content=payload,
        duration_ms=int((time.monotonic() - t0) * 1000),
        result_bytes=len(str(payload)),
        metadata={"retrieval_source": tool_name, "cache_hit": True},
    )


def _fresh_result(tool_name: str, payload: dict[str, Any], t0: float) -> ToolResult:
    return ToolResult(
        content=payload,
        duration_ms=int((time.monotonic() - t0) * 1000),
        result_bytes=len(str(payload)),
        metadata={
            "retrieval_source": tool_name,
            "cache_hit": False,
            "paperclip_result_id": payload.get("result_id"),
        },
    )
