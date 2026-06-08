"""Europe PMC REST search.

Covers PubMed + life-sciences preprints (incl. bioRxiv, medRxiv) and many full-text records.
No API key required.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..base import ToolCtx, ToolResult

EUROPE_PMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePMCSearchTool:
    name = "europe_pmc_search"
    description = (
        "Search Europe PMC (PubMed + bioRxiv/medRxiv + full-text where available). Returns "
        "{id, source, title, abstract, authors, journal, year, doi, url, is_open_access}."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "open_access_only": {"type": "boolean", "default": False},
            "sort": {"type": "string", "default": "relevance"},
        },
        "required": ["query"],
    }

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        query = args.get("query", "").strip()
        n = int(args.get("max_results") or 10)
        oa = bool(args.get("open_access_only"))
        sort = _normalize_sort(args.get("sort"))
        if not query:
            return ToolResult(is_error=True, error_message="empty query")
        q = f"({query}) AND OPEN_ACCESS:Y" if oa else query

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {"query": q, "format": "json", "pageSize": n, "resultType": "core"}
                if sort != "relevance":
                    params["sort"] = sort
                r = await client.get(
                    EUROPE_PMC_URL,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            return ToolResult(is_error=True, error_message=f"europe_pmc failed: {e}")

        results = []
        for hit in data.get("resultList", {}).get("result", [])[:n]:
            pmid = hit.get("pmid")
            doi = hit.get("doi")
            results.append(
                {
                    "id": hit.get("id"),
                    "source": hit.get("source"),
                    "title": hit.get("title"),
                    "abstract": hit.get("abstractText", ""),
                    "authors": hit.get("authorString", ""),
                    "journal": hit.get("journalTitle"),
                    "year": hit.get("pubYear"),
                    "doi": doi,
                    "url": (
                        f"https://europepmc.org/article/{hit.get('source','MED')}/{hit.get('id','')}"
                        if hit.get("id")
                        else None
                    ),
                    "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                    "is_open_access": hit.get("isOpenAccess") == "Y",
                }
            )
        payload = {"query": q, "n": len(results), "results": results}
        return ToolResult(
            content=payload,
            duration_ms=int((time.monotonic() - t0) * 1000),
            result_bytes=len(str(payload)),
            metadata={"retrieval_source": self.name},
        )


def _normalize_sort(value: Any) -> str:
    sort = str(value or "relevance")
    if sort == "recent":
        return "P_PDATE_D desc"
    return sort
