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


class EvidenceBundle(BaseModel):
    session_id: str
    workflow: str
    objective: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    local_sources: list[LocalEvidenceSource] = Field(default_factory=list)
    planned_searches: list[PlannedEvidenceSearch] = Field(default_factory=list)
    source_accounting: list[SourceAccountingEntry] = Field(default_factory=list)
    deduplication_keys: dict[str, list[str]] = Field(default_factory=dict)
    clinical_or_translational: bool = False
    summary: str = ""
    artifact_id: str | None = None
    artifact_path: str | None = None


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
            if "paperclip_search" in names:
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
            if "europe_pmc_search" in names:
                searches.append(PlannedEvidenceSearch(
                    priority=priority,
                    source="europe_pmc",
                    tool="europe_pmc_search",
                    query=query,
                    args=_lane_args(cfg, "europe_pmc", "europe_pmc_search", query, lane),
                    reason="Europe PMC biomedical literature search after Paperclip and OpenAlex.",
                ))
                priority += 1
            if "pubmed_search" in names:
                searches.append(PlannedEvidenceSearch(
                    priority=priority,
                    source="pubmed",
                    tool="pubmed_search",
                    query=query,
                    args=_lane_args(cfg, "pubmed", "pubmed_search", query, lane),
                    reason="Peer-reviewed biomedical literature follow-up search.",
                ))
                priority += 1
            if "arxiv_search" in names:
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
                    args=_lane_args(cfg, "clinical_trials", "clinical_trials_search", query, lane),
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
