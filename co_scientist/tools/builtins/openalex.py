"""OpenAlex work search.

OpenAlex is useful for broad scholarly discovery across disciplines, citation
counts, author metadata, and DOI-based source chasing. No API key is required.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..base import ToolCtx, ToolResult
from ..cache import RetrievalCache

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


class OpenAlexSearchTool:
    name = "openalex_search"
    description = (
        "Search OpenAlex scholarly works across disciplines. Returns title, reconstructed "
        "abstract, authors, year, DOI, journal/source, cited_by_count, and URL."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "sort": {
                "type": "string",
                "enum": ["relevance", "publication_date", "cited_by_count"],
                "default": "relevance",
            },
        },
        "required": ["query"],
    }

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        query = args.get("query", "").strip()
        n = int(args.get("max_results") or 10)
        sort = _normalize_sort(args.get("sort"))
        if not query:
            return ToolResult(is_error=True, error_message="empty query")
        cache_args = {"query": query, "max_results": n, "sort": sort}
        cached = RetrievalCache(ctx.cfg, ctx.session_id).read(self.name, cache_args)
        if cached is not None:
            return ToolResult(
                content=cached,
                duration_ms=int((time.monotonic() - t0) * 1000),
                result_bytes=len(str(cached)),
                metadata={"retrieval_source": self.name, "cache_hit": True},
            )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "search": query,
                    "per-page": n,
                    "select": (
                        "id,doi,display_name,publication_year,cited_by_count,"
                        "authorships,primary_location,abstract_inverted_index"
                    ),
                }
                if sort_param := _openalex_sort_param(sort):
                    params["sort"] = sort_param
                response = await client.get(
                    OPENALEX_WORKS_URL,
                    params=params,
                )
                response.raise_for_status()
                records = _normalize_openalex(response.json(), limit=n)
        except httpx.HTTPError as e:
            return ToolResult(is_error=True, error_message=f"openalex failed: {e}")

        payload = {"query": query, "sort": sort, "n": len(records), "results": records}
        RetrievalCache(ctx.cfg, ctx.session_id).write(self.name, cache_args, payload)
        return ToolResult(
            content=payload,
            duration_ms=int((time.monotonic() - t0) * 1000),
            result_bytes=len(str(payload)),
            metadata={"retrieval_source": self.name, "cache_hit": False},
        )


def _normalize_sort(value: Any) -> str:
    sort = str(value or "relevance")
    if sort in {"publication_date", "cited_by_count"}:
        return sort
    return "relevance"


def _openalex_sort_param(sort: str) -> str | None:
    if sort == "publication_date":
        return "publication_date:desc"
    if sort == "cited_by_count":
        return "cited_by_count:desc"
    return None


def _normalize_openalex(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for work in payload.get("results", [])[:limit]:
        location = work.get("primary_location") or {}
        source = location.get("source") or {}
        records.append(
            {
                "id": work.get("id"),
                "title": work.get("display_name") or "",
                "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
                "authors": [
                    author_name
                    for a in work.get("authorships", [])[:8]
                    if (author_name := (a.get("author") or {}).get("display_name"))
                ],
                "year": work.get("publication_year"),
                "doi": _clean_doi(work.get("doi")),
                "journal": source.get("display_name"),
                "cited_by_count": work.get("cited_by_count") or 0,
                "url": location.get("landing_page_url") or work.get("doi") or work.get("id"),
            }
        )
    return records


def _reconstruct_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    by_position: dict[int, str] = {}
    for token, positions in index.items():
        for pos in positions:
            by_position[int(pos)] = token
    return " ".join(by_position[pos] for pos in sorted(by_position))


def _clean_doi(value: str | None) -> str | None:
    if not value:
        return None
    return value.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
