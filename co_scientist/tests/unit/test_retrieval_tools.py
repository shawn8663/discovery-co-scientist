"""Tests for built-in scientific retrieval tools."""

from __future__ import annotations

from co_scientist.tools.base import ToolCtx
from co_scientist.tools.builtins.clinical_trials import (
    ClinicalTrialsSearchTool,
    _normalize_clinical_trials,
)
from co_scientist.tools.builtins.europe_pmc import EuropePMCSearchTool
from co_scientist.tools.builtins.openalex import OpenAlexSearchTool, _normalize_openalex
from co_scientist.tools.cache import RetrievalCache
from co_scientist.tools.registry import ToolRegistry


def test_registry_discovers_expanded_retrieval_tools(tmp_cfg) -> None:
    reg = ToolRegistry(tmp_cfg).discover()
    names = {tool.name for tool in reg.all()}
    assert "openalex_search" in names
    assert "clinical_trials_search" in names


def test_generation_reflection_evolution_can_use_expanded_retrieval(tmp_cfg) -> None:
    reg = ToolRegistry(tmp_cfg).discover()
    for agent in ("generation", "reflection", "evolution"):
        names = {tool.name for tool in reg.tools_for(agent)}
        assert "openalex_search" in names
        assert "clinical_trials_search" in names


def test_openalex_normalizes_work_records() -> None:
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1000/example",
                "display_name": "A useful paper",
                "publication_year": 2025,
                "cited_by_count": 12,
                "authorships": [
                    {"author": {"display_name": "Ada Lovelace"}},
                    {"author": {"display_name": "Grace Hopper"}},
                ],
                "primary_location": {
                    "source": {"display_name": "Journal of Useful Results"},
                    "landing_page_url": "https://example.org/work",
                },
                "abstract_inverted_index": {
                    "Useful": [0],
                    "abstract": [1],
                },
            }
        ]
    }

    records = _normalize_openalex(payload, limit=10)

    assert records == [
        {
            "id": "https://openalex.org/W123",
            "title": "A useful paper",
            "abstract": "Useful abstract",
            "authors": ["Ada Lovelace", "Grace Hopper"],
            "year": 2025,
            "doi": "10.1000/example",
            "journal": "Journal of Useful Results",
            "cited_by_count": 12,
            "url": "https://example.org/work",
        }
    ]


def test_clinical_trials_normalizes_study_records() -> None:
    payload = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": "NCT00000001",
                        "briefTitle": "Example Trial",
                    },
                    "statusModule": {"overallStatus": "RECRUITING", "startDateStruct": {"date": "2026-01"}},
                    "descriptionModule": {"briefSummary": "A concise trial summary."},
                    "conditionsModule": {"conditions": ["AML"]},
                    "designModule": {"phases": ["PHASE2"], "studyType": "INTERVENTIONAL"},
                    "armsInterventionsModule": {
                        "interventions": [{"name": "Drug A"}, {"name": "Drug B"}]
                    },
                }
            }
        ]
    }

    records = _normalize_clinical_trials(payload, limit=10)

    assert records == [
        {
            "nct_id": "NCT00000001",
            "title": "Example Trial",
            "summary": "A concise trial summary.",
            "status": "RECRUITING",
            "conditions": ["AML"],
            "interventions": ["Drug A", "Drug B"],
            "phases": ["PHASE2"],
            "study_type": "INTERVENTIONAL",
            "start_date": "2026-01",
            "url": "https://clinicaltrials.gov/study/NCT00000001",
        }
    ]


async def test_openalex_uses_retrieval_cache_when_available(tmp_cfg, monkeypatch) -> None:
    args = {"query": "AML drug repurposing", "max_results": 2, "sort": "cited_by_count"}
    cached = {
        "query": args["query"],
        "sort": args["sort"],
        "n": 1,
        "results": [{"title": "Cached work", "url": "https://example.org"}],
    }
    RetrievalCache(tmp_cfg, "ses_cache").write("openalex_search", args, cached)

    class ExplodingClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            raise AssertionError("network should not be used on cache hit")

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("co_scientist.tools.builtins.openalex.httpx.AsyncClient", ExplodingClient)

    result = await OpenAlexSearchTool().call(args, ToolCtx(cfg=tmp_cfg, session_id="ses_cache"))

    assert result.is_error is False
    assert result.content == cached
    assert result.metadata["cache_hit"] is True
    assert result.metadata["retrieval_source"] == "openalex_search"


async def test_openalex_sort_affects_request_and_cache_key(tmp_cfg, monkeypatch) -> None:
    captured_params = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class CaptureClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, _url, *, params):
            captured_params.append(params)
            return Response()

    monkeypatch.setattr("co_scientist.tools.builtins.openalex.httpx.AsyncClient", CaptureClient)

    args = {"query": "AML drug repurposing", "max_results": 2, "sort": "publication_date"}
    result = await OpenAlexSearchTool().call(args, ToolCtx(cfg=tmp_cfg, session_id="ses_sort"))

    assert result.is_error is False
    assert captured_params[0]["sort"] == "publication_date:desc"
    assert result.content["sort"] == "publication_date"
    assert RetrievalCache(tmp_cfg, "ses_sort").read("openalex_search", args) == result.content
    assert RetrievalCache(tmp_cfg, "ses_sort").read(
        "openalex_search",
        {"query": "AML drug repurposing", "max_results": 2, "sort": "cited_by_count"},
    ) is None


async def test_openalex_supported_sorts_map_to_request_params(tmp_cfg, monkeypatch) -> None:
    captured_params = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class CaptureClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, _url, *, params):
            captured_params.append(params)
            return Response()

    monkeypatch.setattr("co_scientist.tools.builtins.openalex.httpx.AsyncClient", CaptureClient)

    cases = [
        ("relevance", None),
        ("publication_date", "publication_date:desc"),
        ("cited_by_count", "cited_by_count:desc"),
    ]
    for idx, (sort, expected_param) in enumerate(cases):
        result = await OpenAlexSearchTool().call(
            {"query": "AML drug repurposing", "max_results": 2, "sort": sort},
            ToolCtx(cfg=tmp_cfg, session_id=f"ses_openalex_sort_{idx}"),
        )

        assert result.is_error is False
        assert result.content["sort"] == sort
        if expected_param is None:
            assert "sort" not in captured_params[idx]
        else:
            assert captured_params[idx]["sort"] == expected_param


async def test_europe_pmc_recent_sort_affects_request(tmp_cfg, monkeypatch) -> None:
    captured_params = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"resultList": {"result": []}}

    class CaptureClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, _url, *, params):
            captured_params.append(params)
            return Response()

    monkeypatch.setattr("co_scientist.tools.builtins.europe_pmc.httpx.AsyncClient", CaptureClient)

    result = await EuropePMCSearchTool().call(
        {"query": "AML drug repurposing", "max_results": 2, "sort": "P_PDATE_D desc"},
        ToolCtx(cfg=tmp_cfg, session_id="ses_epmc"),
    )

    assert result.is_error is False
    assert captured_params[0]["sort"] == "P_PDATE_D desc"


async def test_europe_pmc_recent_mode_maps_to_publication_date_sort(tmp_cfg, monkeypatch) -> None:
    captured_params = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"resultList": {"result": []}}

    class CaptureClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, _url, *, params):
            captured_params.append(params)
            return Response()

    monkeypatch.setattr("co_scientist.tools.builtins.europe_pmc.httpx.AsyncClient", CaptureClient)

    result = await EuropePMCSearchTool().call(
        {"query": "AML drug repurposing", "max_results": 2, "sort": "recent"},
        ToolCtx(cfg=tmp_cfg, session_id="ses_epmc_recent"),
    )

    assert result.is_error is False
    assert captured_params[0]["sort"] == "P_PDATE_D desc"


async def test_clinical_trials_uses_retrieval_cache_when_available(tmp_cfg, monkeypatch) -> None:
    args = {"query": "AML", "max_results": 2}
    cached = {"query": "AML", "n": 1, "results": [{"nct_id": "NCT00000001"}]}
    RetrievalCache(tmp_cfg, "ses_cache").write("clinical_trials_search", args, cached)

    class ExplodingClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            raise AssertionError("network should not be used on cache hit")

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(
        "co_scientist.tools.builtins.clinical_trials.httpx.AsyncClient",
        ExplodingClient,
    )

    result = await ClinicalTrialsSearchTool().call(args, ToolCtx(cfg=tmp_cfg, session_id="ses_cache"))

    assert result.is_error is False
    assert result.content == cached
    assert result.metadata["cache_hit"] is True
    assert result.metadata["retrieval_source"] == "clinical_trials_search"
