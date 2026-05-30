"""OpenAlex work search.

OpenAlex is useful for broad scholarly discovery across disciplines, citation
counts, author metadata, and DOI-based source chasing. No API key is required.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..base import ToolCtx, ToolResult

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
        },
        "required": ["query"],
    }

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        query = args.get("query", "").strip()
        n = int(args.get("max_results") or 10)
        if not query:
            return ToolResult(is_error=True, error_message="empty query")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    OPENALEX_WORKS_URL,
                    params={
                        "search": query,
                        "per-page": n,
                        "select": (
                            "id,doi,display_name,publication_year,cited_by_count,"
                            "authorships,primary_location,abstract_inverted_index"
                        ),
                    },
                )
                response.raise_for_status()
                records = _normalize_openalex(response.json(), limit=n)
        except httpx.HTTPError as e:
            return ToolResult(is_error=True, error_message=f"openalex failed: {e}")

        payload = {"query": query, "n": len(records), "results": records}
        return ToolResult(
            content=payload,
            duration_ms=int((time.monotonic() - t0) * 1000),
            result_bytes=len(str(payload)),
        )


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
