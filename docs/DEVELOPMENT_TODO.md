# Co-Scientist Remaining Development To-Do

This checklist tracks the remaining roadmap work after the current branch stack.
Items are ordered by implementation value: finish observability and efficiency
before broadening the executable scientific skill surface.

## Current Branch Stack Completed

- [x] Evaluation and augmented app roadmap.
- [x] Classifier fail-closed safety foundation.
- [x] Supervisor goal safety gate.
- [x] Hypothesis quarantine safety gate.
- [x] Final overview safety gate.
- [x] Workspace artifact manifest.
- [x] Science-skill metadata, provenance, and approval policy foundation.
- [x] Workspace artifact UI.
- [x] OpenAlex and ClinicalTrials retrieval tools.
- [x] Retrieval cache foundation.
- [x] Local PDF indexing and search.
- [x] Responsive workspace artifact preview layout.
- [x] Clustered duplicate suppression before full Reflection.
- [x] Proximity reclustering scheduled before Reflection when enough hypotheses exist.

## P1: Efficiency and Observability

- [x] Add duplicate-rate metrics.
  - Count generated hypotheses, deterministic duplicates, semantic duplicates,
    clustered duplicates retired before reflection, and duplicates that reach
    tournament.
  - Surface the counts in the benchmark metrics output.
  - Add unit tests around the new counters.

- [x] Add retrieval latency and cache-hit metrics.
  - Record cache hits/misses for OpenAlex, ClinicalTrials, and local PDFs.
  - Record tool latency per retrieval source.
  - Add a small metrics summary to benchmark output.

- [x] Add ranking cost and latency traces.
  - Track match duration, model used, prompt/cache/output tokens, and cost.
  - Aggregate matches per dollar and ranking prompt tokens per match.
  - Preserve compatibility with existing batch ranking behavior.

- [x] Add cluster-aware tournament pairing.
  - Avoid pairing hypotheses from the same dedup cluster when alternatives exist.
  - Prefer cross-cluster comparisons for low-match hypotheses.
  - Add regression tests for same-cluster avoidance and fallback behavior.

- [x] Add cheap first-pass Reflection.
  - Introduce a lightweight review mode before retrieval-heavy full review.
  - Promote only promising hypotheses to full Reflection.
  - Add tests proving low-promise hypotheses skip expensive retrieval tools.

- [x] Add prompt compression for Ranking.
  - Feed Ranking structured review digests instead of full review bodies where possible.
  - Preserve full details for final overview and top-ranked debate paths.
  - Measure ranking prompt token reduction.

## P1: Safety and Security

- [x] Add prompt-injection fixtures for retrieved content.
  - Cover malicious abstracts, markdown, HTML, and local PDF text.
  - Assert untrusted content remains quoted and cannot alter agent instructions.

- [x] Add oversized-download and local-PDF safety tests.
  - Verify configured byte limits.
  - Verify parser failures do not crash sessions.
  - Verify path traversal is rejected for local project files.

- [x] Tighten science-skill secret injection.
  - Inject only skill-declared `required_secrets`.
  - Log which secret names were made available without logging secret values.
  - Add tests for missing, allowed, and disallowed secret access.

- [x] Add visible approval gates for risky skill runs.
  - Require approval when `requires_approval` is true.
  - Require approval for broad write scope, network access, or sensitive safety levels.
  - Add UI/API tests for approval-required and approved execution.

- [x] Add final-report UI status for withheld reports.
  - Show when final output was blocked or redacted by the safety gate.
  - Link to the safety rationale artifact.
  - Add web UI regression coverage.

## P1: Paper Fidelity and Regression Benchmarks

- [x] Add Supplement 8 orchestration tests.
  - Assert generation schedules reflection, reflection schedules ranking,
    ranking schedules tournament batches, and idle flow schedules evolution and
    meta-review under expected conditions.

- [ ] Add Supplement 9 prompt contract tests.
  - Assert required sections exist in generation, reflection, ranking,
    evolution, meta-review, and final overview prompts.
  - Assert parser-required terminal tool names appear where needed.

- [ ] Add AML-style regression benchmark report.
  - Compare multi-agent flow against raw LLM baselines.
  - Report quality, cost, latency, duplicate rate, retrieval hits, and gold-set recall.
  - Save outputs as reproducible benchmark artifacts.

## P2: Augmented Scientist Workspace

- [ ] Add project file upload/index workflow.
  - Register uploaded PDFs, notes, and datasets as workspace artifacts.
  - Trigger local indexing for supported file types.
  - Add UI tests for upload and artifact registration.

- [ ] Add retrieved-literature workspace artifacts.
  - Save PubMed, Europe PMC, arXiv, OpenAlex, ClinicalTrials, and local-PDF
    search results as `retrieved_literature` artifacts.
  - Store source, query, cache key, and citation metadata.

- [ ] Add citation graph and claims table artifacts.
  - Extract claim-to-source mappings from reviews and final overviews.
  - Store structured `citation` artifacts for later drafting.

- [ ] Add analysis artifact workflow.
  - Capture skill stdout, stderr, figures, notebooks, result tables, and logs.
  - Make analysis artifacts discoverable from the session UI.

- [ ] Add publication drafting artifacts.
  - Generate outline, claims table, manuscript sections, figures list, and
    reviewer-response drafts.
  - Preserve citations and provenance for each drafted claim.

## P2: Curated Scientific Skill Packs

- [ ] Add retrieval skill adapters.
  - Prioritize PubMed, Europe PMC, arXiv, OpenAlex, ClinicalTrials, UniProt,
    PubChem, ChEMBL, OpenTargets, and local PDFs.

- [ ] Add analysis skill adapters.
  - Start with vetted Python workflows for tabular analysis, statistics,
    exploratory data analysis, and publication figures.

- [ ] Add drafting skill adapters.
  - Add structured manuscript section generation, abstract drafting, claims
    tables, citation maps, and reviewer-response drafts.

- [ ] Add resumable skill-run support.
  - Reuse existing artifacts when the same skill input hash has already run.
  - Add tests for failed, timed-out, resumed, and cached skill runs.

## Suggested Next Implementation Slice

Continue with Supplement 9 prompt contract tests, because the orchestration
handoffs are now pinned and the prompt/parser contracts need the same guardrails.
