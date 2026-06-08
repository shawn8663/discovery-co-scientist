"""Initial evidence bundle construction and retrieval planning."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from co_scientist.models import ResearchPlan, Session
from co_scientist.models.robin import DiscoveryWorkflow
from co_scientist.retrieval import build_evidence_bundle, latest_evidence_summary
from co_scientist.tools.registry import ToolRegistry
from co_scientist.workspace import ScientistWorkspace


def _session(plan: ResearchPlan, *, workflow: DiscoveryWorkflow = "therapeutic_discovery") -> Session:
    return Session(
        id="ses_evidence",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="running",
        workflow=workflow,
        research_goal=plan.objective,
        research_plan=plan,
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )


@pytest.mark.asyncio
async def test_evidence_bundle_prioritizes_project_files_and_plans_sources(tmp_cfg) -> None:
    tmp_cfg.paperclip.enabled = True
    tmp_cfg.secrets.OPENALEX_API_KEY = "openalex-key"
    tmp_cfg.secrets.PAPERCLIP_API_KEY = "paperclip-key"
    plan = ResearchPlan(
        objective="Find therapeutic compounds that inhibit aberrant microtubule formation in cancer",
        retrieval_queries=[
            "microtubule formation cancer progression",
            "microtubule inhibitors tolerability clinical trial",
        ],
        clinical_or_translational=True,
    )
    session = _session(plan)
    source = tmp_cfg.data_dir / "background.txt"
    source.write_text("Background paper DOI 10.1234/example. PMID: 12345678")
    ScientistWorkspace(tmp_cfg, session.id).add_artifact(
        kind="project_file",
        path=source,
        title="Background DNA transfer paper",
        metadata={"content_type": "text/plain", "indexed": True},
    )

    bundle = await build_evidence_bundle(tmp_cfg, session, ToolRegistry(tmp_cfg).discover())

    assert bundle.local_sources[0].title == "Background DNA transfer paper"
    assert bundle.deduplication_keys["doi"] == ["10.1234/example"]
    assert bundle.deduplication_keys["pmid"] == ["12345678"]
    assert bundle.planned_searches[0].tool == "local_pdf_search"
    first_query_searches = [
        search for search in bundle.planned_searches
        if search.query == "microtubule formation cancer progression"
    ]
    assert [search.source for search in first_query_searches[:4]] == [
        "uploaded_project_files",
        "paperclip",
        "openalex",
        "europe_pmc",
    ]
    enabled_tools = {search.tool for search in bundle.planned_searches if search.enabled}
    assert {"pubmed_search", "europe_pmc_search", "arxiv_search"}.issubset(enabled_tools)
    assert "openalex_search" in enabled_tools
    assert "paperclip_search" in enabled_tools
    assert "clinical_trials_search" in enabled_tools
    assert "biorxiv_medrxiv" in {search.source for search in bundle.planned_searches}
    assert len(bundle.source_accounting) == len(bundle.local_sources) + len(bundle.planned_searches)
    local_entry = bundle.source_accounting[0]
    assert local_entry.source_id == "src_local_001"
    assert local_entry.status == "local_cataloged"
    assert local_entry.artifact_id == bundle.local_sources[0].artifact_id
    assert local_entry.identifiers["doi"] == ["10.1234/example"]
    planned_entry = next(entry for entry in bundle.source_accounting if entry.source_id == "src_plan_001")
    assert planned_entry.status == "planned"
    assert planned_entry.tool == "local_pdf_search"
    assert planned_entry.query == "microtubule formation cancer progression"

    manifest = ScientistWorkspace(tmp_cfg, session.id).list()
    evidence_artifacts = [artifact for artifact in manifest if artifact.kind == "evidence_bundle"]
    assert len(evidence_artifacts) == 1
    payload = json.loads(Path(evidence_artifacts[0].path).read_text())
    assert payload["summary"] == latest_evidence_summary(tmp_cfg, session.id)
    assert payload["source_accounting"][0]["source_id"] == "src_local_001"
    assert "src_local_001" in payload["summary"]
    assert "src_plan_001" in payload["summary"]
    assert "Uploaded project/background files cataloged first: 1" in payload["summary"]


@pytest.mark.asyncio
async def test_evidence_bundle_records_disabled_optional_sources_without_keys(tmp_cfg, monkeypatch) -> None:
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
    tmp_cfg.secrets.OPENALEX_API_KEY = ""
    tmp_cfg.secrets.PAPERCLIP_API_KEY = ""
    tmp_cfg.paperclip.enabled = True
    plan = ResearchPlan(
        objective="Explain somatic mutation and horizontal DNA transfer in cancer",
        retrieval_queries=["horizontal DNA transfer cancer genome instability"],
    )
    session = _session(plan, workflow="general_hypothesis")

    bundle = await build_evidence_bundle(tmp_cfg, session, ToolRegistry(tmp_cfg).discover())

    disabled = {(search.source, search.enabled_reason) for search in bundle.planned_searches if not search.enabled}
    assert ("openalex", "OPENALEX_API_KEY not configured") in disabled
    assert ("paperclip", "PAPERCLIP_API_KEY not configured") in disabled
    assert "clinical_trials_search" not in {search.tool for search in bundle.planned_searches}
    disabled_accounting = [
        entry for entry in bundle.source_accounting
        if entry.status == "disabled"
    ]
    assert {entry.source_type for entry in disabled_accounting} == {"openalex", "paperclip"}
