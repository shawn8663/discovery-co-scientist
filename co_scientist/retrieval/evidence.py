"""Initial evidence bundle construction.

The bundle is intentionally lightweight: it makes uploaded project files the
first-class background source, records deduplication anchors for them, and gives
agents an ordered retrieval plan for external sources.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..config import Config
from ..ids import artifact_id
from ..models import ResearchPlan, Session
from ..tools.base import ToolCtx
from ..tools.registry import ToolRegistry
from ..workspace import ScientistWorkspace, WorkspaceArtifact

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_PMID_RE = re.compile(r"\bPMID[:\s]+(\d{4,12})\b", re.IGNORECASE)
_ARXIV_RE = re.compile(r"\barXiv[:\s]+([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)\b", re.IGNORECASE)
_CLINICAL_TERMS = (
    "clinical", "translational", "therapeutic", "therapy", "treatment",
    "compound", "drug", "trial", "patient", "human", "safety",
    "tolerability", "delivery", "adme", "pharmacokinetic", "pk",
    "diagnostic", "biomarker",
)
_SUPPORTED_RANKING_MODES = ("relevance", "recent", "impact")


class LocalEvidenceSource(BaseModel):
    artifact_id: str
    title: str
    path: str
    content_type: str = ""
    size_bytes: int = 0
    sha256: str | None = None
    indexed: bool = False
    identifiers: dict[str, list[str]] = Field(default_factory=dict)


class PlannedEvidenceSearch(BaseModel):
    priority: int
    source: str
    tool: str
    query: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    enabled: bool = True
    enabled_reason: str = ""


class SourceAccountingEntry(BaseModel):
    source_id: str
    source_type: str
    status: str
    title: str = ""
    tool: str | None = None
    query: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    artifact_id: str | None = None
    path: str | None = None
    url: str | None = None
    identifiers: dict[str, list[str]] = Field(default_factory=dict)
    sha256: str | None = None
    duplicate_of: str | None = None
    reason: str = ""
    enabled_reason: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    result_count: int | None = None
    duration_ms: int | None = None
    result_bytes: int | None = None
    result_metadata: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class EvidenceRecord(BaseModel):
    canonical_id: str = ""
    title: str = ""
    abstract: str = ""
    authors: str | list[str] = ""
    year: int | None = None
    url: str | None = None
    source_type: str = ""
    identifiers: dict[str, list[str]] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    source_hits: list[dict[str, Any]] = Field(default_factory=list)
    relevance_score: float = 0.0
    impact_score: float = 0.0
    recency_score: float = 0.0
    corroboration_score: float = 0.0
    total_score: float = 0.0
    groups: list[str] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    session_id: str
    workflow: str
    objective: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    local_sources: list[LocalEvidenceSource] = Field(default_factory=list)
    planned_searches: list[PlannedEvidenceSearch] = Field(default_factory=list)
    source_accounting: list[SourceAccountingEntry] = Field(default_factory=list)
    deduplication_keys: dict[str, list[str]] = Field(default_factory=dict)
    canonical_evidence: list[EvidenceRecord] = Field(default_factory=list)
    evidence_groups: dict[str, list[str]] = Field(default_factory=dict)
    clinical_or_translational: bool = False
    summary: str = ""
    artifact_id: str | None = None
    artifact_path: str | None = None


def apply_retrieval_overrides(
    cfg: Config,
    *,
    max_results_per_source: int | None = None,
    ranking_modes: str | None = None,
) -> None:
    """Apply validated evidence retrieval overrides to config in-place."""
    if max_results_per_source is not None:
        limit = _validate_max_results_per_source(max_results_per_source)
        cfg.evidence_retrieval.default_limit = limit
        cfg.evidence_retrieval.local_limit = min(limit, 20)
        cfg.evidence_retrieval.paperclip_limit = min(limit, 1000)
        capped_external_limit = min(limit, 50)
        cfg.evidence_retrieval.openalex_limit = capped_external_limit
        cfg.evidence_retrieval.pubmed_limit = capped_external_limit
        cfg.evidence_retrieval.europe_pmc_limit = capped_external_limit
        cfg.evidence_retrieval.arxiv_limit = capped_external_limit
        cfg.evidence_retrieval.preprint_limit = capped_external_limit
        cfg.evidence_retrieval.clinical_trials_limit = capped_external_limit
    if ranking_modes is not None:
        cfg.evidence_retrieval.ranking_modes = _parse_ranking_modes(ranking_modes)


def apply_retrieval_settings_from_text(cfg: Config, text: str) -> None:
    """Parse a simple retrieval_settings block from prompt text and apply it."""
    settings: dict[str, str] = {}
    in_block = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not in_block:
            if stripped == "retrieval_settings:":
                in_block = True
            continue
        if not stripped:
            break
        if ":" not in stripped:
            break
        key, value = stripped.split(":", 1)
        key = key.strip()
        if key not in {"max_results_per_source", "ranking_modes"}:
            continue
        settings[key] = value.strip()

    if not settings:
        return
    max_results = None
    if "max_results_per_source" in settings:
        raw_limit = settings["max_results_per_source"]
        try:
            max_results = int(raw_limit)
        except ValueError as exc:
            raise ValueError(
                "retrieval_settings.max_results_per_source must be an integer from 1 to 1000."
            ) from exc
    apply_retrieval_overrides(
        cfg,
        max_results_per_source=max_results,
        ranking_modes=settings.get("ranking_modes"),
    )


async def build_evidence_bundle(
    cfg: Config,
    session: Session,
    tools: ToolRegistry,
) -> EvidenceBundle:
    """Create and persist the initial evidence bundle for a new session."""
    bundle = EvidenceBundle(
        session_id=session.id,
        workflow=session.workflow,
        objective=session.research_plan.objective,
        local_sources=_catalog_local_sources(cfg, session.id),
        clinical_or_translational=_clinical_or_translational(session.research_plan, session.workflow),
    )
    bundle.deduplication_keys = _deduplication_keys(bundle.local_sources)
    bundle.planned_searches = _planned_searches(cfg, session.research_plan, tools, bundle)
    bundle.source_accounting = _source_accounting(bundle)
    bundle.summary = _render_summary(bundle)

    workspace = ScientistWorkspace(cfg, session.id)
    workspace.ensure()
    aid = artifact_id()
    path = workspace.root / "evidence" / f"{aid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.model_dump(mode="json")
    payload["artifact_id"] = aid
    payload["artifact_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    artifact = workspace.add_artifact(
        kind="evidence_bundle",
        path=path,
        title="Initial evidence bundle",
        provenance={
            "stage": "session_start",
            "workflow": session.workflow,
            "prompt_fields": ["retrieval_queries", "clinical_or_translational"],
        },
        metadata={
            "n_local_sources": len(bundle.local_sources),
            "n_planned_searches": len(bundle.planned_searches),
            "clinical_or_translational": bundle.clinical_or_translational,
        },
    )
    bundle.artifact_id = artifact.id
    bundle.artifact_path = str(path)
    _write_bundle(bundle)
    return bundle


async def execute_evidence_searches(
    cfg: Config,
    session_id: str,
    bundle: EvidenceBundle,
    tools: ToolRegistry,
) -> EvidenceBundle:
    """Execute enabled planned searches and persist result accounting."""
    normalized_records: list[EvidenceRecord] = []
    for entry in bundle.source_accounting:
        if not entry.source_id.startswith("src_plan_"):
            continue
        if entry.status == "disabled":
            continue
        if not entry.tool:
            entry.status = "failed"
            entry.error_message = "missing tool name"
            continue
        result = await tools.call(
            entry.tool,
            dict(entry.args),
            ToolCtx(cfg=cfg, session_id=session_id, run_id=entry.source_id),
        )
        entry.duration_ms = result.duration_ms
        entry.result_bytes = result.result_bytes
        entry.result_metadata = dict(result.metadata or {})
        if result.artifact_path:
            entry.path = result.artifact_path
        if result.is_error:
            entry.status = "failed"
            entry.error_message = result.error_message or "tool call failed"
            entry.result_count = 0
        else:
            entry.status = "executed"
            entry.result_count = _result_count(result.content)
            entry.error_message = None
            normalized_records.extend(normalize_retrieval_records(
                source_id=entry.source_id,
                source_type=entry.source_type,
                tool=entry.tool,
                query=entry.query or "",
                lane=str(entry.args.get("lane") or "relevance"),
                content=result.content,
            ))
    bundle.canonical_evidence = build_canonical_evidence(cfg, normalized_records)
    bundle.evidence_groups = _group_index(
        bundle.canonical_evidence,
        limit=cfg.evidence_retrieval.group_limit,
    )
    bundle.summary = _render_summary(bundle)
    _write_bundle(bundle)
    return bundle


def latest_evidence_summary(cfg: Config, session_id: str) -> str:
    """Return the most recent evidence bundle summary, if one exists."""
    workspace = ScientistWorkspace(cfg, session_id)
    bundles = [a for a in workspace.list() if a.kind == "evidence_bundle"]
    if not bundles:
        return ""
    latest = max(bundles, key=lambda artifact: artifact.created_at)
    try:
        payload = json.loads(Path(latest.path).read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("summary") or "")


def normalize_retrieval_records(
    *,
    source_id: str,
    source_type: str,
    tool: str,
    query: str,
    lane: str,
    content: Any,
) -> list[EvidenceRecord]:
    """Convert raw retrieval payloads into source-aware evidence records."""
    if not isinstance(content, dict):
        return []
    raw_results = content.get("results")
    if not isinstance(raw_results, list):
        return []

    records: list[EvidenceRecord] = []
    source_hit = {
        "source_id": source_id,
        "source_type": source_type,
        "tool": tool,
        "query": query,
        "lane": lane,
    }
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        identifiers: dict[str, list[str]] = {}
        doi = _normalize_doi(raw.get("doi"))
        if doi:
            identifiers["doi"] = [doi]
        pmid = raw.get("pmid") or raw.get("pubmed_id")
        if pmid:
            identifiers["pmid"] = [str(pmid)]
        arxiv_id = raw.get("arxiv_id")
        if arxiv_id:
            identifiers["arxiv"] = [str(arxiv_id)]

        metrics: dict[str, Any] = {}
        if "cited_by_count" in raw:
            metrics["cited_by_count"] = raw["cited_by_count"]

        records.append(EvidenceRecord(
            title=str(raw.get("title") or raw.get("display_name") or raw.get("name") or ""),
            abstract=str(raw.get("abstract") or raw.get("summary") or ""),
            authors=raw.get("authors") or "",
            year=_safe_int(raw.get("year") or raw.get("publication_year")),
            url=raw.get("url") or raw.get("abs_url") or raw.get("pubmed_url"),
            source_type=source_type,
            identifiers=identifiers,
            metrics=metrics,
            source_hits=[dict(source_hit)],
        ))
    return records


def build_canonical_evidence(cfg: Config, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Deduplicate normalized evidence records and assign canonical scores."""
    canonical_by_key: dict[str, EvidenceRecord] = {}
    key_order: list[str] = []
    for i, record in enumerate(records):
        key = _canonical_key(record) or f"record:{i}"
        if key not in canonical_by_key:
            canonical = record.model_copy(deep=True)
            canonical.canonical_id = key
            canonical_by_key[key] = canonical
            key_order.append(key)
            continue
        _merge_evidence_record(canonical_by_key[key], record)

    canonical = [canonical_by_key[key] for key in key_order]
    _score_and_group_records(cfg, canonical)
    canonical.sort(key=lambda item: item.total_score, reverse=True)
    return canonical[:cfg.evidence_retrieval.max_canonical_items]


def _group_index(records: list[EvidenceRecord], *, limit: int) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for record in records:
        for group in record.groups:
            groups.setdefault(group, [])
            if len(groups[group]) < limit:
                groups[group].append(record.canonical_id)
    return groups


def _canonical_key(record: EvidenceRecord) -> str:
    for kind in ("doi", "pmid", "arxiv"):
        values = record.identifiers.get(kind, [])
        if values:
            value = str(values[0]).strip()
            if value:
                if kind == "doi":
                    value = _normalize_doi(value)
                return f"{kind}:{value.lower()}"
    if record.url:
        return f"url:{record.url.strip()}"
    title = _normalize_title(record.title)
    return f"title:{title}" if title else ""


def _merge_identifier_dicts(
    a: dict[str, list[str]],
    b: dict[str, list[str]],
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {kind: list(values) for kind, values in a.items()}
    for kind, values in b.items():
        out = merged.setdefault(kind, [])
        seen = {str(value) for value in out}
        for value in values:
            value_text = str(value)
            if value_text in seen:
                continue
            out.append(value)
            seen.add(value_text)
    return {kind: values for kind, values in merged.items() if values}


def _merge_evidence_record(target: EvidenceRecord, incoming: EvidenceRecord) -> None:
    if not target.abstract and incoming.abstract:
        target.abstract = incoming.abstract
    if target.year is None and incoming.year is not None:
        target.year = incoming.year
    if not target.url and incoming.url:
        target.url = incoming.url
    if not target.source_type and incoming.source_type:
        target.source_type = incoming.source_type
    if not target.authors and incoming.authors:
        target.authors = incoming.authors

    target.identifiers = _merge_identifier_dicts(target.identifiers, incoming.identifiers)
    target.metrics = _merge_metrics(target.metrics, incoming.metrics)
    target.source_hits.extend(dict(hit) for hit in incoming.source_hits)


def _merge_metrics(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    merged = dict(a)
    for key, value in b.items():
        if key == "cited_by_count":
            merged_count = _safe_int(merged.get(key))
            incoming_count = _safe_int(value)
            if incoming_count is None:
                continue
            if merged_count is None or incoming_count > merged_count:
                merged[key] = incoming_count
            continue
        if key not in merged or merged[key] in (None, ""):
            merged[key] = value
    return merged


def _score_and_group_records(cfg: Config, records: list[EvidenceRecord]) -> None:
    citation_counts = [_safe_int(record.metrics.get("cited_by_count")) or 0 for record in records]
    max_citations = max(citation_counts, default=0)
    current_year = datetime.now(UTC).year
    weights = cfg.evidence_retrieval
    for record, cited_by_count in zip(records, citation_counts, strict=True):
        source_types = _source_types_for_record(record)
        has_relevance_hit = any(hit.get("lane") == "relevance" for hit in record.source_hits)
        record.relevance_score = 1.0 if has_relevance_hit else 0.4
        record.impact_score = cited_by_count / max_citations if max_citations else 0.0
        record.recency_score = _recency_score(record.year, current_year)
        record.corroboration_score = min(1.0, len(source_types) / 3)
        record.total_score = (
            weights.relevance_weight * record.relevance_score
            + weights.impact_weight * record.impact_score
            + weights.recency_weight * record.recency_score
            + weights.corroboration_weight * record.corroboration_score
        )
        record.groups = _groups_for_record(record)


def _recency_score(year: int | None, current_year: int) -> float:
    if year is None:
        return 0.0
    age = max(0, current_year - year)
    return max(0.0, 1.0 - (age / 20))


def _groups_for_record(record: EvidenceRecord) -> list[str]:
    groups = {"highest_relevance"}
    cited_by_count = _safe_int(record.metrics.get("cited_by_count")) or 0
    if record.impact_score >= 0.75 or cited_by_count >= 100:
        groups.add("highest_impact")
    if record.recency_score >= 0.8:
        groups.add("newest")
    source_types = _source_types_for_record(record)
    if source_types.intersection({"biorxiv_medrxiv", "arxiv"}):
        groups.add("preprints")
    if "clinical_trials" in source_types:
        groups.add("clinical_translational")
    return sorted(groups)


def _source_types_for_record(record: EvidenceRecord) -> set[str]:
    source_types = {
        str(hit.get("source_type"))
        for hit in record.source_hits
        if hit.get("source_type")
    }
    if record.source_type:
        source_types.add(record.source_type)
    return source_types


def _write_bundle(bundle: EvidenceBundle) -> None:
    if not bundle.artifact_path:
        return
    path = Path(bundle.artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")


def _result_count(content: Any) -> int:
    if isinstance(content, dict):
        n = content.get("n")
        if isinstance(n, int):
            return n
        results = content.get("results")
        if isinstance(results, list):
            return len(results)
    if isinstance(content, list):
        return len(content)
    return 0


def _catalog_local_sources(cfg: Config, session_id: str) -> list[LocalEvidenceSource]:
    workspace = ScientistWorkspace(cfg, session_id)
    out: list[LocalEvidenceSource] = []
    for artifact in workspace.list():
        if artifact.kind != "project_file":
            continue
        path = Path(artifact.path)
        size = path.stat().st_size if path.is_file() else 0
        digest = _sha256(path) if path.is_file() else None
        text = _local_text_preview(cfg, artifact, path)
        out.append(LocalEvidenceSource(
            artifact_id=artifact.id,
            title=artifact.title or path.name,
            path=str(path),
            content_type=str(artifact.metadata.get("content_type", "")),
            size_bytes=size,
            sha256=digest,
            indexed=bool(artifact.metadata.get("indexed")),
            identifiers=_extract_identifiers(text),
        ))
    return out


def _planned_searches(
    cfg: Config,
    plan: ResearchPlan,
    tools: ToolRegistry,
    bundle: EvidenceBundle,
) -> list[PlannedEvidenceSearch]:
    names = {tool.name for tool in tools.all()}
    queries = _queries(plan)
    searches: list[PlannedEvidenceSearch] = []
    priority = 0
    for query in queries:
        if "local_pdf_search" in names:
            searches.append(PlannedEvidenceSearch(
                priority=priority,
                source="uploaded_project_files",
                tool="local_pdf_search",
                query=query,
                args=_lane_args(cfg, "uploaded_project_files", "local_pdf_search", query, "relevance"),
                reason="Search uploaded PDFs/project files before external retrieval.",
            ))
            priority += 1

    lanes = _unique_lanes(cfg.evidence_retrieval.ranking_modes)
    for query in queries:
        for lane in lanes:
            if "paperclip_search" in names and lane in {"relevance", "recent"}:
                searches.append(PlannedEvidenceSearch(
                    priority=priority,
                    source="paperclip",
                    tool="paperclip_search",
                    query=query,
                    args=_lane_args(cfg, "paperclip", "paperclip_search", query, lane),
                    reason=(
                        "Primary external literature pass; Paperclip SDK mediates "
                        "search access and returned literature context."
                    ),
                    enabled=cfg.paperclip.enabled and _has_secret(cfg, "PAPERCLIP_API_KEY"),
                    enabled_reason=(
                        _secret_reason(cfg, "PAPERCLIP_API_KEY")
                        if cfg.paperclip.enabled
                        else "paperclip disabled in config"
                    ),
                ))
                priority += 1
            if "openalex_search" in names:
                searches.append(PlannedEvidenceSearch(
                    priority=priority,
                    source="openalex",
                    tool="openalex_search",
                    query=query,
                    args=_lane_args(cfg, "openalex", "openalex_search", query, lane),
                    reason="OpenAlex API scholarly graph search after Paperclip.",
                    enabled=_has_secret(cfg, "OPENALEX_API_KEY"),
                    enabled_reason=_secret_reason(cfg, "OPENALEX_API_KEY"),
                ))
                priority += 1
            if "europe_pmc_search" in names and lane in {"relevance", "recent"}:
                searches.append(PlannedEvidenceSearch(
                    priority=priority,
                    source="europe_pmc",
                    tool="europe_pmc_search",
                    query=query,
                    args=_lane_args(cfg, "europe_pmc", "europe_pmc_search", query, lane),
                    reason="Europe PMC biomedical literature search after Paperclip and OpenAlex.",
                ))
                priority += 1
            if "pubmed_search" in names and lane in {"relevance", "recent"}:
                searches.append(PlannedEvidenceSearch(
                    priority=priority,
                    source="pubmed",
                    tool="pubmed_search",
                    query=query,
                    args=_lane_args(cfg, "pubmed", "pubmed_search", query, lane),
                    reason="Peer-reviewed biomedical literature follow-up search.",
                ))
                priority += 1
            if "arxiv_search" in names and lane in {"relevance", "recent"}:
                searches.append(PlannedEvidenceSearch(
                    priority=priority,
                    source="arxiv",
                    tool="arxiv_search",
                    query=query,
                    args=_lane_args(cfg, "arxiv", "arxiv_search", query, lane),
                    reason="Computational, quantitative biology, and methods literature follow-up search.",
                ))
                priority += 1
        if bundle.clinical_or_translational and "clinical_trials_search" in names:
            searches.append(PlannedEvidenceSearch(
                priority=priority,
                source="clinical_trials",
                tool="clinical_trials_search",
                query=query,
                args=_lane_args(cfg, "clinical_trials", "clinical_trials_search", query, "relevance"),
                reason="Clinical/translational goal: inspect registered human studies.",
            ))
            priority += 1
        if "europe_pmc_search" in names:
            preprint_query = f'({query}) AND (SRC:PPR OR JOURNAL:"bioRxiv" OR JOURNAL:"medRxiv")'
            preprint_lane = "recent" if "recent" in lanes else "relevance"
            searches.append(PlannedEvidenceSearch(
                priority=priority,
                source="biorxiv_medrxiv",
                tool="europe_pmc_search",
                query=preprint_query,
                args=_lane_args(
                    cfg,
                    "biorxiv_medrxiv",
                    "europe_pmc_search",
                    preprint_query,
                    preprint_lane,
                ),
                reason="Explicit life-science preprint follow-up pass through Europe PMC.",
            ))
            priority += 1

    return searches


def _queries(plan: ResearchPlan) -> list[str]:
    raw = [q.strip() for q in plan.retrieval_queries if q and q.strip()]
    if not raw:
        raw = [plan.objective.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for query in raw:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(query[:240])
    return out[:6]


def _unique_lanes(lanes: list[str]) -> list[str]:
    out: list[str] = []
    for lane in lanes:
        if lane not in out:
            out.append(lane)
    return out or ["relevance"]


def _validate_max_results_per_source(value: int) -> int:
    if isinstance(value, bool) or value < 1 or value > 1000:
        raise ValueError("max_results_per_source must be an integer from 1 to 1000.")
    return value


def _parse_ranking_modes(value: str) -> list[str]:
    modes = _unique_lanes([part.strip() for part in value.split(",") if part.strip()])
    invalid = [mode for mode in modes if mode not in _SUPPORTED_RANKING_MODES]
    if invalid:
        allowed = ",".join(_SUPPORTED_RANKING_MODES)
        bad = ",".join(invalid)
        raise ValueError(f"Unsupported ranking_modes: {bad}. Supported modes: {allowed}.")
    return modes


def _limit_for_source(cfg: Config, source: str) -> int:
    limits = {
        "uploaded_project_files": cfg.evidence_retrieval.local_limit,
        "paperclip": cfg.evidence_retrieval.paperclip_limit,
        "openalex": cfg.evidence_retrieval.openalex_limit,
        "pubmed": cfg.evidence_retrieval.pubmed_limit,
        "europe_pmc": cfg.evidence_retrieval.europe_pmc_limit,
        "arxiv": cfg.evidence_retrieval.arxiv_limit,
        "biorxiv_medrxiv": cfg.evidence_retrieval.preprint_limit,
        "clinical_trials": cfg.evidence_retrieval.clinical_trials_limit,
    }
    return limits.get(source, cfg.evidence_retrieval.default_limit)


def _lane_args(cfg: Config, source: str, tool: str, query: str, lane: str) -> dict[str, Any]:
    args: dict[str, Any] = {
        "query": query,
        "max_results": _limit_for_source(cfg, source),
        "lane": lane,
    }
    if source == "uploaded_project_files":
        args["max_chars"] = 4000
    if tool == "paperclip_search":
        args["sort"] = "date" if lane == "recent" else "relevance"
    elif tool == "openalex_search":
        args["sort"] = {
            "relevance": "relevance",
            "recent": "publication_date",
            "impact": "cited_by_count",
        }.get(lane, "relevance")
    elif tool == "pubmed_search":
        args["sort"] = "pub_date" if lane == "recent" else "relevance"
    elif tool == "arxiv_search":
        args["sort"] = "submitted" if lane == "recent" else "relevance"
    elif tool == "europe_pmc_search" and lane == "recent":
        args["sort"] = "P_PDATE_D desc"
    return args


def _clinical_or_translational(plan: ResearchPlan, workflow: str) -> bool:
    if plan.clinical_or_translational or workflow == "therapeutic_discovery":
        return True
    haystack = " ".join(
        [
            plan.objective,
            plan.domain_hint or "",
            " ".join(plan.preferences),
            " ".join(plan.constraints),
            plan.notes or "",
            plan.retrieval_notes or "",
        ]
    ).lower()
    return any(term in haystack for term in _CLINICAL_TERMS)


def _deduplication_keys(sources: list[LocalEvidenceSource]) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {"sha256": [], "doi": [], "pmid": [], "arxiv": [], "title": []}
    for source in sources:
        if source.sha256:
            keys["sha256"].append(source.sha256)
        for kind in ("doi", "pmid", "arxiv"):
            keys[kind].extend(source.identifiers.get(kind, []))
        title_key = _normalize_title(source.title)
        if title_key:
            keys["title"].append(title_key)
    return {k: sorted(set(v)) for k, v in keys.items() if v}


def _source_accounting(bundle: EvidenceBundle) -> list[SourceAccountingEntry]:
    entries: list[SourceAccountingEntry] = []
    for i, source in enumerate(bundle.local_sources, start=1):
        entries.append(SourceAccountingEntry(
            source_id=f"src_local_{i:03d}",
            source_type="uploaded_project_file",
            status="local_cataloged",
            title=source.title,
            artifact_id=source.artifact_id,
            path=source.path,
            identifiers=source.identifiers,
            sha256=source.sha256,
            reason="Uploaded project/background file; prioritized before all external retrieval.",
            provenance={
                "content_type": source.content_type,
                "size_bytes": source.size_bytes,
                "indexed": source.indexed,
            },
        ))
    for i, search in enumerate(bundle.planned_searches, start=1):
        entries.append(SourceAccountingEntry(
            source_id=f"src_plan_{i:03d}",
            source_type=search.source,
            status="planned" if search.enabled else "disabled",
            title=f"{search.source}: {search.query}",
            tool=search.tool,
            query=search.query,
            args=search.args,
            reason=search.reason,
            enabled_reason=search.enabled_reason or ("enabled" if search.enabled else ""),
            provenance={
                "priority": search.priority,
                "workflow": bundle.workflow,
                "objective": bundle.objective,
            },
        ))
    return entries


def _render_summary(bundle: EvidenceBundle) -> str:
    lines = [
        "Initial evidence bundle:",
        f"- Objective: {bundle.objective}",
        f"- Workflow: {bundle.workflow}",
        f"- Uploaded project/background files cataloged first: {len(bundle.local_sources)}",
    ]
    if bundle.local_sources:
        local_entries = [entry for entry in bundle.source_accounting if entry.status == "local_cataloged"]
        titles = ", ".join(f"{entry.source_id}: {entry.title}" for entry in local_entries[:6])
        lines.append(f"- Local sources: {titles}")
    if bundle.deduplication_keys:
        key_counts = ", ".join(f"{k}={len(v)}" for k, v in sorted(bundle.deduplication_keys.items()))
        lines.append(f"- Deduplication anchors from local files: {key_counts}")
    enabled = [s for s in bundle.planned_searches if s.enabled]
    disabled = [s for s in bundle.planned_searches if not s.enabled]
    source_order = []
    for search in enabled:
        if search.source not in source_order:
            source_order.append(search.source)
    lines.append("- Planned retrieval order: " + ", ".join(source_order[:12]))
    if disabled:
        disabled_sources = sorted({f"{s.source} ({s.enabled_reason})" for s in disabled})
        lines.append("- Optional sources not enabled: " + "; ".join(disabled_sources))
    result_entries = [
        entry for entry in bundle.source_accounting
        if entry.status in {"executed", "failed"}
    ]
    if result_entries:
        executed = [entry for entry in result_entries if entry.status == "executed"]
        failed = [entry for entry in result_entries if entry.status == "failed"]
        total_results = sum(entry.result_count or 0 for entry in executed)
        lines.append(
            f"- Executed source results: {len(executed)} executed, "
            f"{len(failed)} failed, {total_results} total result records"
        )
    if bundle.canonical_evidence:
        lines.append(
            f"- Canonical evidence records after deduplication: {len(bundle.canonical_evidence)}"
        )
        for group, ids in sorted(bundle.evidence_groups.items()):
            lines.append(f"  - {group}: {len(ids)} records")
    ledger_preview = [
        entry for entry in bundle.source_accounting
        if entry.status != "local_cataloged"
    ][:24]
    if ledger_preview:
        lines.append("- Traceable source ledger:")
        for entry in ledger_preview:
            status = entry.status
            tool = entry.tool or entry.source_type
            query = (entry.query or "").replace("\n", " ")[:120]
            suffix = ""
            if entry.status == "executed":
                suffix = f" ({entry.result_count or 0} results)"
            elif entry.status == "failed" and entry.error_message:
                suffix = f" (failed: {entry.error_message[:80]})"
            lines.append(f"  - {entry.source_id} [{status}] {tool}: {query}{suffix}")
        remaining = len(bundle.source_accounting) - len([e for e in bundle.source_accounting if e.status == "local_cataloged"]) - len(ledger_preview)
        if remaining > 0:
            lines.append(f"  - ... {remaining} additional planned source entries in the evidence bundle artifact")
    if bundle.clinical_or_translational:
        lines.append("- Clinical/translational context detected: include ClinicalTrials.gov evidence.")
    lines.append(
        "Use source_id values when citing support from this bundle; deduplicate external hits "
        "against src_local_* entries and preserve tool/query provenance for later reports."
    )
    return "\n".join(lines)


def _local_text_preview(cfg: Config, artifact: WorkspaceArtifact, path: Path) -> str:
    if not path.is_file():
        return ""
    if _looks_like_pdf(artifact):
        try:
            from ..tools.local_pdf_search import _read_or_index_pdf

            indexed, _cache_hit = _read_or_index_pdf(cfg, artifact, path)
            return str(indexed.get("text") or "")[:40_000]
        except Exception:
            return ""
    if path.suffix.lower() in {".txt", ".md", ".json", ".csv", ".tsv"}:
        try:
            return path.read_text(errors="ignore")[:40_000]
        except OSError:
            return ""
    return ""


def _looks_like_pdf(artifact: WorkspaceArtifact) -> bool:
    path = artifact.path.lower()
    content_type = str(artifact.metadata.get("content_type", "")).lower()
    return path.endswith(".pdf") or content_type == "application/pdf"


def _extract_identifiers(text: str) -> dict[str, list[str]]:
    return {
        "doi": sorted({m.group(0).rstrip(".,;") for m in _DOI_RE.finditer(text)}),
        "pmid": sorted({m.group(1) for m in _PMID_RE.finditer(text)}),
        "arxiv": sorted({m.group(1) for m in _ARXIV_RE.finditer(text)}),
    }


def _normalize_doi(value: Any) -> str:
    if value is None:
        return ""
    doi = str(value).strip().lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if doi.startswith(prefix):
            return doi.removeprefix(prefix)
    return doi


def _safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_title(title: str) -> str:
    text = re.sub(r"\.[A-Za-z0-9]+$", "", title.strip().lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _has_secret(cfg: Config, name: str) -> bool:
    return bool(getattr(cfg.secrets, name, "") or os.environ.get(name))


def _secret_reason(cfg: Config, name: str) -> str:
    return f"{name} configured" if _has_secret(cfg, name) else f"{name} not configured"
