# Multi-Lane Evidence Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable multi-lane literature retrieval, source-aware result normalization, deduplicated canonical evidence, and transparent ranking/grouping to evidence bundle creation.

**Architecture:** Keep raw tool outputs exactly as retrieved for provenance, then build a second canonical evidence layer from those raw artifacts. Evidence planning becomes configurable through `EvidenceRetrievalCfg`, CLI options, and optional prompt/input-file settings; downstream agents consume a compact ranked/grouped summary while reports can trace every canonical item back to original source hits.

**Tech Stack:** Python 3.12, Pydantic models, Typer CLI, SQLite/workspace artifacts, existing `ToolRegistry`, pytest, Ruff.

---

## File Structure

- Modify `co_scientist/config.py`
  - Add `EvidenceRetrievalCfg` with per-source limits, ranking modes, lane defaults, and scoring weights.
- Modify `co_scientist/retrieval/evidence.py`
  - Add retrieval settings models, lane-aware planning, raw result capture, canonical evidence construction, deduplication, scoring, grouping, and summary rendering.
- Modify `co_scientist/retrieval/__init__.py`
  - Export any new public evidence models/helpers needed by tests and future UI/API code.
- Modify `co_scientist/cli.py`
  - Add evidence/run CLI options for result depth and ranking modes.
- Modify `co_scientist/tools/builtins/openalex.py`
  - Add optional DOI/PMID lookup support for metadata enrichment if needed by canonicalization.
- Add or modify `co_scientist/tests/unit/test_evidence_bundle.py`
  - Cover settings, multi-lane planning, canonical deduplication, scoring, grouping, and artifact persistence.
- Modify `co_scientist/tests/unit/test_cli_evidence.py`
  - Cover CLI options and prompt-file retrieval settings.
- Add `docs/EVIDENCE_RETRIEVAL.md`
  - Document retrieval lanes, limits, deduplication, ranking, and traceability.

---

## Task 1: Add Retrieval Settings Model

**Files:**
- Modify: `co_scientist/config.py`
- Modify: `co_scientist/retrieval/evidence.py`
- Modify: `co_scientist/tests/unit/test_evidence_bundle.py`

- [ ] **Step 1: Write failing config/model tests**

Add this test to `co_scientist/tests/unit/test_evidence_bundle.py`:

```python
def test_evidence_retrieval_config_defaults_are_balanced(tmp_cfg) -> None:
    cfg = tmp_cfg.evidence_retrieval

    assert cfg.depth == "balanced"
    assert cfg.default_limit == 25
    assert cfg.local_limit == 20
    assert cfg.paperclip_limit == 50
    assert cfg.openalex_limit == 25
    assert cfg.pubmed_limit == 25
    assert cfg.europe_pmc_limit == 25
    assert cfg.arxiv_limit == 15
    assert cfg.preprint_limit == 15
    assert cfg.clinical_trials_limit == 25
    assert cfg.ranking_modes == ["relevance", "recent", "impact"]
    assert cfg.retain_raw_results is True
    assert cfg.deduplicate_canonical_evidence is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_evidence_retrieval_config_defaults_are_balanced -q
```

Expected: fail with `AttributeError` because `Config.evidence_retrieval` does not exist.

- [ ] **Step 3: Implement config model**

In `co_scientist/config.py`, add this model near `WebSearchCfg` and include it in `Config`:

```python
class EvidenceRetrievalCfg(BaseModel):
    depth: Literal["quick", "balanced", "comprehensive"] = "balanced"
    default_limit: int = 25
    local_limit: int = 20
    paperclip_limit: int = 50
    openalex_limit: int = 25
    pubmed_limit: int = 25
    europe_pmc_limit: int = 25
    arxiv_limit: int = 15
    preprint_limit: int = 15
    clinical_trials_limit: int = 25
    ranking_modes: list[Literal["relevance", "recent", "impact"]] = Field(
        default_factory=lambda: ["relevance", "recent", "impact"]
    )
    retain_raw_results: bool = True
    deduplicate_canonical_evidence: bool = True
    max_canonical_items: int = 200
    group_limit: int = 25
    relevance_weight: float = 0.45
    impact_weight: float = 0.25
    recency_weight: float = 0.20
    corroboration_weight: float = 0.10
```

Then add this field to `Config`:

```python
evidence_retrieval: EvidenceRetrievalCfg = Field(default_factory=EvidenceRetrievalCfg)
```

- [ ] **Step 4: Run focused test**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_evidence_retrieval_config_defaults_are_balanced -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add co_scientist/config.py co_scientist/tests/unit/test_evidence_bundle.py
git commit -m "Add evidence retrieval configuration"
```

---

## Task 2: Plan Multi-Lane Searches

**Files:**
- Modify: `co_scientist/retrieval/evidence.py`
- Modify: `co_scientist/tests/unit/test_evidence_bundle.py`

- [ ] **Step 1: Write failing multi-lane planning test**

Add this test to `co_scientist/tests/unit/test_evidence_bundle.py`:

```python
@pytest.mark.asyncio
async def test_evidence_bundle_plans_relevance_recent_and_impact_lanes(tmp_cfg) -> None:
    tmp_cfg.paperclip.enabled = True
    tmp_cfg.secrets.PAPERCLIP_API_KEY = "paperclip-key"
    tmp_cfg.secrets.OPENALEX_API_KEY = "openalex-key"
    tmp_cfg.evidence_retrieval.ranking_modes = ["relevance", "recent", "impact"]
    plan = ResearchPlan(
        objective="Find somatic mutation accumulation literature",
        retrieval_queries=["somatic mutation accumulation cancer aging"],
    )
    session = _session(plan, workflow="general_hypothesis")

    bundle = await build_evidence_bundle(tmp_cfg, session, ToolRegistry(tmp_cfg).discover())

    lane_keys = [
        (search.source, search.args.get("lane"), search.args.get("sort"))
        for search in bundle.planned_searches
        if search.query == "somatic mutation accumulation cancer aging"
    ]
    assert ("paperclip", "relevance", "relevance") in lane_keys
    assert ("paperclip", "recent", "date") in lane_keys
    assert ("openalex", "impact", "cited_by_count") in lane_keys
    assert ("openalex", "recent", "publication_date") in lane_keys
    assert ("pubmed", "recent", "pub_date") in lane_keys
    assert all(search.args["max_results"] <= 50 for search in bundle.planned_searches)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_evidence_bundle_plans_relevance_recent_and_impact_lanes -q
```

Expected: fail because only one lane per source exists.

- [ ] **Step 3: Add lane helpers**

In `co_scientist/retrieval/evidence.py`, add:

```python
def _limit_for_source(cfg: Config, source: str) -> int:
    er = cfg.evidence_retrieval
    limits = {
        "uploaded_project_files": er.local_limit,
        "paperclip": er.paperclip_limit,
        "openalex": er.openalex_limit,
        "pubmed": er.pubmed_limit,
        "europe_pmc": er.europe_pmc_limit,
        "arxiv": er.arxiv_limit,
        "biorxiv_medrxiv": er.preprint_limit,
        "clinical_trials": er.clinical_trials_limit,
    }
    return limits.get(source, er.default_limit)


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
        }[lane]
    elif tool == "pubmed_search":
        args["sort"] = "pub_date" if lane == "recent" else "relevance"
    elif tool == "arxiv_search":
        args["sort"] = "submitted" if lane == "recent" else "relevance"
    return args
```

- [ ] **Step 4: Replace single-lane external planning**

In `_planned_searches`, keep local search first, then loop over lanes for each external source:

```python
lanes = list(dict.fromkeys(cfg.evidence_retrieval.ranking_modes))
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

for query in queries:
    for lane in lanes:
        # Paperclip, OpenAlex, Europe PMC, PubMed, arXiv, preprints, clinical trials.
        # Use existing enabled/enabled_reason logic and _lane_args for args.
```

When adding `biorxiv_medrxiv`, use the explicit preprint query and `lane="recent"` only if the lane list contains `recent`; otherwise use `lane="relevance"`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py -q
```

Expected: pass after updating existing call-order assertions to account for lanes.

- [ ] **Step 6: Commit**

```bash
git add co_scientist/retrieval/evidence.py co_scientist/tests/unit/test_evidence_bundle.py
git commit -m "Plan multi-lane evidence searches"
```

---

## Task 3: Normalize Raw Retrieval Results

**Files:**
- Modify: `co_scientist/retrieval/evidence.py`
- Modify: `co_scientist/retrieval/__init__.py`
- Modify: `co_scientist/tests/unit/test_evidence_bundle.py`

- [ ] **Step 1: Write failing normalization test**

Add this test:

```python
def test_normalize_retrieval_records_extracts_identifiers_and_metrics() -> None:
    from co_scientist.retrieval.evidence import normalize_retrieval_records

    records = normalize_retrieval_records(
        source_id="src_plan_004",
        source_type="openalex",
        tool="openalex_search",
        query="somatic mutation aging",
        lane="impact",
        content={
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Somatic mutation in aging",
                    "doi": "https://doi.org/10.1234/example",
                    "year": 2024,
                    "cited_by_count": 120,
                    "url": "https://example.org/paper",
                }
            ]
        },
    )

    assert len(records) == 1
    record = records[0]
    assert record.title == "Somatic mutation in aging"
    assert record.identifiers["doi"] == ["10.1234/example"]
    assert record.metrics["cited_by_count"] == 120
    assert record.source_hits[0]["source_id"] == "src_plan_004"
    assert record.source_hits[0]["lane"] == "impact"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_normalize_retrieval_records_extracts_identifiers_and_metrics -q
```

Expected: fail because `normalize_retrieval_records` does not exist.

- [ ] **Step 3: Add normalized record model and function**

In `co_scientist/retrieval/evidence.py`, add:

```python
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


def normalize_retrieval_records(
    *,
    source_id: str,
    source_type: str,
    tool: str,
    query: str,
    lane: str,
    content: Any,
) -> list[EvidenceRecord]:
    raw_results = content.get("results", []) if isinstance(content, dict) else []
    records: list[EvidenceRecord] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("display_name") or raw.get("name") or "").strip()
        doi = _normalize_doi(raw.get("doi"))
        pmid = str(raw.get("pmid") or raw.get("pubmed_id") or "").strip()
        arxiv_id = str(raw.get("arxiv_id") or "").strip()
        identifiers: dict[str, list[str]] = {}
        if doi:
            identifiers["doi"] = [doi]
        if pmid:
            identifiers["pmid"] = [pmid]
        if arxiv_id:
            identifiers["arxiv"] = [arxiv_id]
        year = _safe_int(raw.get("year") or raw.get("publication_year"))
        metrics = {}
        if raw.get("cited_by_count") is not None:
            metrics["cited_by_count"] = _safe_int(raw.get("cited_by_count")) or 0
        records.append(EvidenceRecord(
            title=title,
            abstract=str(raw.get("abstract") or raw.get("summary") or ""),
            authors=raw.get("authors") or "",
            year=year,
            url=raw.get("url") or raw.get("abs_url") or raw.get("pubmed_url"),
            source_type=source_type,
            identifiers=identifiers,
            metrics=metrics,
            source_hits=[{
                "source_id": source_id,
                "source_type": source_type,
                "tool": tool,
                "query": query,
                "lane": lane,
            }],
        ))
    return records
```

Also add helpers:

```python
def _normalize_doi(value: Any) -> str:
    doi = str(value or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.lower()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Export model/helper**

In `co_scientist/retrieval/__init__.py`, export `EvidenceRecord` and `normalize_retrieval_records`.

- [ ] **Step 5: Run focused test**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_normalize_retrieval_records_extracts_identifiers_and_metrics -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add co_scientist/retrieval/evidence.py co_scientist/retrieval/__init__.py co_scientist/tests/unit/test_evidence_bundle.py
git commit -m "Normalize evidence retrieval records"
```

---

## Task 4: Build Canonical Deduplicated Evidence

**Files:**
- Modify: `co_scientist/retrieval/evidence.py`
- Modify: `co_scientist/tests/unit/test_evidence_bundle.py`

- [ ] **Step 1: Write failing deduplication test**

Add this test:

```python
def test_canonical_evidence_merges_duplicate_records_by_doi_and_preserves_hits(tmp_cfg) -> None:
    from co_scientist.retrieval.evidence import EvidenceRecord, build_canonical_evidence

    records = [
        EvidenceRecord(
            title="Somatic mutation in aging",
            year=2024,
            identifiers={"doi": ["10.1234/example"]},
            metrics={"cited_by_count": 120},
            source_hits=[{"source_id": "src_plan_001", "source_type": "openalex", "lane": "impact"}],
        ),
        EvidenceRecord(
            title="Somatic mutation in aging.",
            year=2024,
            identifiers={"doi": ["10.1234/example"]},
            source_hits=[{"source_id": "src_plan_002", "source_type": "pubmed", "lane": "relevance"}],
        ),
    ]

    canonical = build_canonical_evidence(tmp_cfg, records)

    assert len(canonical) == 1
    item = canonical[0]
    assert item.canonical_id.startswith("doi:10.1234/example")
    assert len(item.source_hits) == 2
    assert item.metrics["cited_by_count"] == 120
    assert "highest_impact" in item.groups
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_canonical_evidence_merges_duplicate_records_by_doi_and_preserves_hits -q
```

Expected: fail because `build_canonical_evidence` does not exist.

- [ ] **Step 3: Add canonical ID and merge helpers**

In `co_scientist/retrieval/evidence.py`, add:

```python
def build_canonical_evidence(cfg: Config, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    merged: dict[str, EvidenceRecord] = {}
    for record in records:
        key = _canonical_key(record)
        existing = merged.get(key)
        if existing is None:
            record.canonical_id = key
            merged[key] = record
            continue
        existing.source_hits.extend(record.source_hits)
        existing.identifiers = _merge_identifier_dicts(existing.identifiers, record.identifiers)
        existing.metrics = {**record.metrics, **existing.metrics}
        if not existing.abstract and record.abstract:
            existing.abstract = record.abstract
        if existing.year is None and record.year is not None:
            existing.year = record.year
    canonical = list(merged.values())
    _score_and_group_records(cfg, canonical)
    return sorted(canonical, key=lambda item: item.total_score, reverse=True)[:cfg.evidence_retrieval.max_canonical_items]


def _canonical_key(record: EvidenceRecord) -> str:
    for kind in ("doi", "pmid", "arxiv"):
        values = record.identifiers.get(kind) or []
        if values:
            return f"{kind}:{values[0].lower()}"
    if record.url:
        return f"url:{record.url.lower()}"
    return f"title:{_normalize_title(record.title)}"
```

Add `_merge_identifier_dicts`:

```python
def _merge_identifier_dicts(a: dict[str, list[str]], b: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for source in (a, b):
        for key, values in source.items():
            out.setdefault(key, set()).update(v for v in values if v)
    return {key: sorted(values) for key, values in out.items()}
```

- [ ] **Step 4: Add scoring/grouping helpers**

Add:

```python
def _score_and_group_records(cfg: Config, records: list[EvidenceRecord]) -> None:
    max_cites = max([int(r.metrics.get("cited_by_count") or 0) for r in records] or [0])
    current_year = datetime.now(UTC).year
    for record in records:
        lanes = {hit.get("lane") for hit in record.source_hits}
        source_types = {hit.get("source_type") for hit in record.source_hits}
        record.relevance_score = 1.0 if "relevance" in lanes else 0.4
        cites = int(record.metrics.get("cited_by_count") or 0)
        record.impact_score = (cites / max_cites) if max_cites else 0.0
        record.recency_score = _recency_score(record.year, current_year)
        record.corroboration_score = min(1.0, len(source_types) / 3)
        weights = cfg.evidence_retrieval
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
    return max(0.0, 1.0 - min(age, 20) / 20)


def _groups_for_record(record: EvidenceRecord) -> list[str]:
    groups = ["highest_relevance"]
    if record.impact_score >= 0.75 or int(record.metrics.get("cited_by_count") or 0) >= 100:
        groups.append("highest_impact")
    if record.recency_score >= 0.8:
        groups.append("newest")
    source_types = {str(hit.get("source_type") or "") for hit in record.source_hits}
    if "biorxiv_medrxiv" in source_types or "arxiv" in source_types:
        groups.append("preprints")
    if "clinical_trials" in source_types:
        groups.append("clinical_translational")
    return sorted(set(groups))
```

- [ ] **Step 5: Run focused test**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_canonical_evidence_merges_duplicate_records_by_doi_and_preserves_hits -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add co_scientist/retrieval/evidence.py co_scientist/tests/unit/test_evidence_bundle.py
git commit -m "Build canonical deduplicated evidence"
```

---

## Task 5: Persist Canonical Evidence in the Bundle

**Files:**
- Modify: `co_scientist/retrieval/evidence.py`
- Modify: `co_scientist/tests/unit/test_evidence_bundle.py`

- [ ] **Step 1: Write failing execution artifact test**

Add this test:

```python
@pytest.mark.asyncio
async def test_execute_evidence_searches_persists_canonical_evidence(tmp_cfg) -> None:
    plan = ResearchPlan(
        objective="Find somatic mutation accumulation literature",
        retrieval_queries=["somatic mutation accumulation cancer aging"],
    )
    session = _session(plan, workflow="general_hypothesis")
    tools = _FakeRegistry(["openalex_search", "pubmed_search"])

    bundle = await build_evidence_bundle(tmp_cfg, session, tools)
    executed = await execute_evidence_searches(tmp_cfg, session.id, bundle, tools)

    assert executed.canonical_evidence
    assert executed.evidence_groups["highest_relevance"]
    payload = json.loads(Path(executed.artifact_path).read_text())
    assert payload["canonical_evidence"]
    assert payload["evidence_groups"]["highest_relevance"]
```

Update `_FakeRegistry.call` so it returns overlapping DOI records:

```python
return ToolResult(
    content={
        "query": args["query"],
        "n": 2,
        "results": [
            {
                "title": "Somatic mutation in aging",
                "doi": "10.1234/example",
                "year": 2024,
                "cited_by_count": 120,
            },
            {"title": "Genome instability and disease", "year": 2023},
        ],
    },
    duration_ms=7,
    result_bytes=128,
    metadata={"retrieval_source": name, "cache_hit": False},
)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_execute_evidence_searches_persists_canonical_evidence -q
```

Expected: fail because `canonical_evidence` and `evidence_groups` do not exist.

- [ ] **Step 3: Add bundle fields**

Add these fields to `EvidenceBundle`:

```python
canonical_evidence: list[EvidenceRecord] = Field(default_factory=list)
evidence_groups: dict[str, list[str]] = Field(default_factory=dict)
```

Add:

```python
def _group_index(records: list[EvidenceRecord], *, limit: int) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for record in records:
        for group in record.groups:
            groups.setdefault(group, [])
            if len(groups[group]) < limit:
                groups[group].append(record.canonical_id)
    return groups
```

- [ ] **Step 4: Build canonical evidence during execution**

Inside `execute_evidence_searches`, accumulate normalized records after each successful tool call:

```python
normalized_records: list[EvidenceRecord] = []
...
if not result.is_error:
    normalized_records.extend(normalize_retrieval_records(
        source_id=entry.source_id,
        source_type=entry.source_type,
        tool=entry.tool,
        query=entry.query or "",
        lane=str(entry.args.get("lane") or "relevance"),
        content=result.content,
    ))
...
bundle.canonical_evidence = build_canonical_evidence(cfg, normalized_records)
bundle.evidence_groups = _group_index(
    bundle.canonical_evidence,
    limit=cfg.evidence_retrieval.group_limit,
)
```

- [ ] **Step 5: Update summary**

In `_render_summary`, add lines:

```python
if bundle.canonical_evidence:
    lines.append(f"- Canonical evidence records after deduplication: {len(bundle.canonical_evidence)}")
    for group, ids in sorted(bundle.evidence_groups.items()):
        lines.append(f"  - {group}: {len(ids)} records")
```

- [ ] **Step 6: Run focused test**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_execute_evidence_searches_persists_canonical_evidence -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add co_scientist/retrieval/evidence.py co_scientist/tests/unit/test_evidence_bundle.py
git commit -m "Persist canonical evidence index"
```

---

## Task 6: Add CLI and Prompt-File Retrieval Overrides

**Files:**
- Modify: `co_scientist/cli.py`
- Modify: `co_scientist/retrieval/evidence.py`
- Modify: `co_scientist/tests/unit/test_cli_evidence.py`

- [ ] **Step 1: Write failing CLI option test**

Add a test to `co_scientist/tests/unit/test_cli_evidence.py` that invokes:

```python
result = runner.invoke(app, [
    "evidence",
    "Find somatic mutation literature",
    "--no-parse-goal",
    "--max-results-per-source",
    "40",
    "--ranking-modes",
    "relevance,recent,impact",
])

assert result.exit_code == 0
assert "max_results_per_source=40" in result.output
assert "ranking_modes=relevance,recent,impact" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_cli_evidence.py -q
```

Expected: fail because the new options do not exist.

- [ ] **Step 3: Add CLI options**

In `co_scientist/cli.py`, add options to the `evidence` command:

```python
max_results_per_source: int | None = typer.Option(
    None,
    "--max-results-per-source",
    min=1,
    max=1000,
    help="Override default result limit for each external evidence source.",
),
ranking_modes: str | None = typer.Option(
    None,
    "--ranking-modes",
    help="Comma-separated retrieval lanes: relevance,recent,impact.",
),
```

Before building the bundle, apply overrides:

```python
if max_results_per_source is not None:
    cfg.evidence_retrieval.default_limit = max_results_per_source
    cfg.evidence_retrieval.openalex_limit = min(max_results_per_source, 50)
    cfg.evidence_retrieval.pubmed_limit = min(max_results_per_source, 50)
    cfg.evidence_retrieval.europe_pmc_limit = min(max_results_per_source, 50)
    cfg.evidence_retrieval.arxiv_limit = min(max_results_per_source, 50)
    cfg.evidence_retrieval.preprint_limit = min(max_results_per_source, 50)
    cfg.evidence_retrieval.clinical_trials_limit = min(max_results_per_source, 50)
    cfg.evidence_retrieval.paperclip_limit = min(max_results_per_source, 1000)
if ranking_modes:
    allowed = {"relevance", "recent", "impact"}
    parsed = [mode.strip() for mode in ranking_modes.split(",") if mode.strip()]
    bad = [mode for mode in parsed if mode not in allowed]
    if bad:
        console.print(f"[red]Unsupported ranking mode(s): {', '.join(bad)}[/red]")
        raise typer.Exit(2)
    cfg.evidence_retrieval.ranking_modes = parsed
```

Print:

```python
console.print(
    "[dim]Evidence retrieval: "
    f"max_results_per_source={cfg.evidence_retrieval.default_limit}, "
    f"ranking_modes={','.join(cfg.evidence_retrieval.ranking_modes)}[/dim]"
)
```

- [ ] **Step 4: Add prompt-file settings parser**

In `co_scientist/retrieval/evidence.py`, add:

```python
def apply_retrieval_settings_from_text(cfg: Config, text: str) -> None:
    match = re.search(r"(?is)^retrieval_settings:\s*(.+?)(?:\n\n|\Z)", text)
    if not match:
        return
    block = match.group(1)
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        if key == "max_results_per_source":
            n = int(value)
            cfg.evidence_retrieval.default_limit = n
            cfg.evidence_retrieval.openalex_limit = min(n, 50)
            cfg.evidence_retrieval.pubmed_limit = min(n, 50)
            cfg.evidence_retrieval.europe_pmc_limit = min(n, 50)
            cfg.evidence_retrieval.paperclip_limit = min(n, 1000)
        elif key == "ranking_modes":
            modes = [mode.strip() for mode in value.split(",") if mode.strip()]
            cfg.evidence_retrieval.ranking_modes = modes
```

Call it from `cli.py` immediately after `effective_goal` is loaded:

```python
from .retrieval import apply_retrieval_settings_from_text
apply_retrieval_settings_from_text(cfg, effective_goal)
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_cli_evidence.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add co_scientist/cli.py co_scientist/retrieval/evidence.py co_scientist/tests/unit/test_cli_evidence.py
git commit -m "Add evidence retrieval CLI overrides"
```

---

## Task 7: Improve Source Tool Sorting Support

**Files:**
- Modify: `co_scientist/tools/builtins/openalex.py`
- Modify: `co_scientist/tools/builtins/europe_pmc.py`
- Modify: `co_scientist/tests/unit/test_evidence_bundle.py`

- [ ] **Step 1: Write failing sort-support tests**

Add tests that call each tool with `sort` in args and monkeypatch HTTP clients if existing test patterns support that. Minimum assertion for this task:

```python
def test_lane_args_request_source_specific_sort_modes(tmp_cfg) -> None:
    from co_scientist.retrieval.evidence import _lane_args

    assert _lane_args(tmp_cfg, "openalex", "openalex_search", "q", "impact")["sort"] == "cited_by_count"
    assert _lane_args(tmp_cfg, "openalex", "openalex_search", "q", "recent")["sort"] == "publication_date"
    assert _lane_args(tmp_cfg, "pubmed", "pubmed_search", "q", "recent")["sort"] == "pub_date"
    assert _lane_args(tmp_cfg, "paperclip", "paperclip_search", "q", "recent")["sort"] == "date"
```

- [ ] **Step 2: Run test**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py::test_lane_args_request_source_specific_sort_modes -q
```

Expected: pass if Task 2 implemented `_lane_args`; otherwise fix `_lane_args`.

- [ ] **Step 3: Implement OpenAlex sort**

In `co_scientist/tools/builtins/openalex.py`, add `sort` to schema and request params:

```python
"sort": {
    "type": "string",
    "enum": ["relevance", "publication_date", "cited_by_count"],
    "default": "relevance",
},
```

Then:

```python
sort = args.get("sort", "relevance")
params = {...}
if sort == "publication_date":
    params["sort"] = "publication_date:desc"
elif sort == "cited_by_count":
    params["sort"] = "cited_by_count:desc"
```

- [ ] **Step 4: Implement Europe PMC sort where possible**

In `co_scientist/tools/builtins/europe_pmc.py`, add a `sort` schema field and use:

```python
sort = args.get("sort", "relevance")
params = {"query": q, "format": "json", "pageSize": n, "resultType": "core"}
if sort == "recent":
    params["sort"] = "FIRST_PDATE_D desc"
```

- [ ] **Step 5: Run source and evidence tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit/test_evidence_bundle.py co_scientist/tests/unit/test_tools_registry.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add co_scientist/tools/builtins/openalex.py co_scientist/tools/builtins/europe_pmc.py co_scientist/tests/unit/test_evidence_bundle.py
git commit -m "Support source-specific evidence sorting"
```

---

## Task 8: Document and Verify End-to-End Behavior

**Files:**
- Add: `docs/EVIDENCE_RETRIEVAL.md`
- Modify: `docs/CO_SCIENTIST_APPLICATION_MANUAL.md`
- Test: full unit suite

- [ ] **Step 1: Add evidence retrieval documentation**

Create `docs/EVIDENCE_RETRIEVAL.md`:

```markdown
# Evidence Retrieval

Discovery Co-Scientist builds evidence bundles before generation. The retrieval layer is optimized separately from hypothesis generation so users can inspect source coverage before spending model budget.

## Source Order

1. Uploaded PDFs and project files
2. Paperclip, when configured and authenticated
3. OpenAlex
4. Europe PMC
5. PubMed
6. arXiv and preprint searches
7. ClinicalTrials.gov for therapeutic or translational goals

## Retrieval Lanes

The default lanes are `relevance`, `recent`, and `impact`. Lanes are source-aware: OpenAlex can sort by citation count, PubMed can sort by publication date, Paperclip can sort by relevance or date, and Europe PMC supports relevance/recent retrieval.

## Raw and Canonical Evidence

Raw results are retained as `retrieved_literature` artifacts. The evidence bundle also contains `canonical_evidence`, a deduplicated index that merges records by DOI, PMID, arXiv ID, URL, and normalized title. Each canonical item preserves `source_hits` so reports can trace claims back to the exact source, query, tool, and lane.

## Ranking

Canonical evidence is scored with relevance, citation/impact, recency, and corroboration components. The score is transparent and stored on each record. Group indexes expose highest relevance, newest, highest impact, preprints, and clinical/translational evidence.

## CLI

```bash
discovery-coscientist evidence \
  --prompt-file initial_prompt.txt \
  --max-results-per-source 50 \
  --ranking-modes relevance,recent,impact
```

Prompt files may include:

```text
retrieval_settings:
  max_results_per_source: 50
  ranking_modes: relevance,recent,impact
```
```

- [ ] **Step 2: Update application manual**

In `docs/CO_SCIENTIST_APPLICATION_MANUAL.md`, add a short section linking to `docs/EVIDENCE_RETRIEVAL.md` and describing `--max-results-per-source` and `--ranking-modes`.

- [ ] **Step 3: Run full tests**

Run:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENROUTER_API_KEY -u GEMINI_API_KEY -u VOYAGE_API_KEY -u TAVILY_API_KEY -u BRAVE_API_KEY -u OPENALEX_API_KEY -u OPENALEX_EMAIL -u NCBI_EMAIL -u NCBI_API_KEY -u PUBMED_API_KEY -u PAPERCLIP_API_KEY uv run pytest co_scientist/tests/unit -q
```

Expected: all unit tests pass.

- [ ] **Step 4: Run Ruff**

Run:

```bash
uv run ruff check co_scientist docs
```

Expected: all checks pass.

- [ ] **Step 5: Install into separate runtime**

Run with approval if required by sandbox:

```bash
/Users/shawnlevy/Codex/discovery-co-scientist/.venv/bin/python -m pip install -e '/Users/shawnlevy/Codex/Co-scientist/repo[paperclip]'
```

Expected: installation succeeds and runtime imports the editable source.

- [ ] **Step 6: Commit and push**

```bash
git add co_scientist docs
git commit -m "Document multi-lane evidence retrieval"
git push discovery implement-local-pdf-index-cache:main
```

---

## Self-Review

- **Spec coverage:** The plan covers configurable limits, multiple retrieval lanes, raw result retention, canonical deduplication, citation/impact-aware scoring, grouping, CLI overrides, prompt-file settings, source sorting support, documentation, tests, runtime install, and push.
- **Placeholder scan:** No task uses open-ended placeholders. Each task names files, test commands, implementation snippets, and expected outcomes.
- **Type consistency:** `EvidenceRetrievalCfg`, `EvidenceRecord`, `canonical_evidence`, `evidence_groups`, `normalize_retrieval_records`, and `build_canonical_evidence` are defined before later tasks use them.
- **Scope check:** This is one cohesive subsystem: evidence retrieval optimization. It is large but testable in independent slices before touching generation or Robin workflows.
