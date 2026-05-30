"""ClinicalTrials.gov v2 search.

This tool helps ground therapeutic hypotheses in registered human studies. No
API key is required.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..base import ToolCtx, ToolResult
from ..cache import RetrievalCache

CLINICAL_TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"


class ClinicalTrialsSearchTool:
    name = "clinical_trials_search"
    description = (
        "Search ClinicalTrials.gov registered studies. Returns NCT ID, title, summary, "
        "status, conditions, interventions, phases, study type, start date, and URL."
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
        cache_args = {"query": query, "max_results": n}
        cached = RetrievalCache(ctx.cfg, ctx.session_id).read(self.name, cache_args)
        if cached is not None:
            return ToolResult(
                content=cached,
                duration_ms=int((time.monotonic() - t0) * 1000),
                result_bytes=len(str(cached)),
            )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    CLINICAL_TRIALS_URL,
                    params={
                        "query.term": query,
                        "pageSize": n,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                records = _normalize_clinical_trials(response.json(), limit=n)
        except httpx.HTTPError as e:
            return ToolResult(is_error=True, error_message=f"clinical_trials failed: {e}")

        payload = {"query": query, "n": len(records), "results": records}
        RetrievalCache(ctx.cfg, ctx.session_id).write(self.name, cache_args, payload)
        return ToolResult(
            content=payload,
            duration_ms=int((time.monotonic() - t0) * 1000),
            result_bytes=len(str(payload)),
        )


def _normalize_clinical_trials(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for study in payload.get("studies", [])[:limit]:
        protocol = study.get("protocolSection") or {}
        ident = protocol.get("identificationModule") or {}
        status = protocol.get("statusModule") or {}
        desc = protocol.get("descriptionModule") or {}
        conditions = protocol.get("conditionsModule") or {}
        design = protocol.get("designModule") or {}
        interventions = protocol.get("armsInterventionsModule") or {}
        nct_id = ident.get("nctId") or ""
        records.append(
            {
                "nct_id": nct_id,
                "title": ident.get("briefTitle") or ident.get("officialTitle") or "",
                "summary": desc.get("briefSummary") or "",
                "status": status.get("overallStatus"),
                "conditions": conditions.get("conditions") or [],
                "interventions": [
                    item.get("name")
                    for item in interventions.get("interventions", [])
                    if item.get("name")
                ],
                "phases": design.get("phases") or [],
                "study_type": design.get("studyType"),
                "start_date": (status.get("startDateStruct") or {}).get("date"),
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None,
            }
        )
    return records
