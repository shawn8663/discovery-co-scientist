# Co-Scientist Evaluation and Augmented App Roadmap

This roadmap translates the uploaded papers and Supplement sections 7-9 into an
implementation audit for this open-source codebase. The repo already contains the
core paper architecture: durable Supervisor, Generation, Reflection, Ranking,
Evolution, Proximity, Meta-review, SQLite state, FAISS embeddings, built-in
literature tools, web UI, safety classifier, and a bridge for executable
scientific skills.

## Paper Fidelity Audit

| Area | Current state | Gap | Priority |
| --- | --- | --- | --- |
| Supervisor orchestration | Durable queue follows Supplement 8: generation creates hypotheses, reflection reviews, ranking adds and runs tournament, idle loop schedules ranking/evolution/metareview, final overview runs on termination. | Goal and hypothesis safety checks are not yet first-class gates in the Supervisor flow. | P0 |
| Agent prompts | Prompt files map closely to Supplement 9 for literature generation, debate generation, ranking, evolution, reflection, and metareview. | Prompt regression tests should assert key required sections and final parser phrases. | P1 |
| Ranking tournament | Elo, batch ranking, debate mode, and match prioritization are implemented. | Add cost/latency traces per match and duplicate-cluster-aware match selection. | P1 |
| Reflection | Full, verification, and observation review modes exist. Clustered duplicate drafts are now retired before full reflection. | Add an explicit cheap initial review before expensive retrieval-heavy review. | P1 |
| Proximity | Embedding-backed clustering exists, can suppress later clustered duplicates before Reflection spends LLM/tool budget, is scheduled ahead of reflection when enough hypotheses exist to recluster, and now exposes duplicate-rate metrics. | Add cluster-aware tournament pairing. | P1 |
| Meta-review | System feedback and final overview exist. | Final report now has a configurable safety gate; add UI status for withheld reports. | P0 |

## Safety and Security Audit

| Finding | Impact | Risk | Cost | Expected gain | Phase |
| --- | --- | --- | --- | --- | --- |
| Classifier previously failed benign on missing key or model errors. | Unsafe goals/reports can pass silently outside dev. | High | Low | Fail-closed production behavior with local dev escape hatch. | P0 |
| Science skills execute local scripts with broad capability. | Malicious or over-broad skills can read files, use network, or mutate state. | High | Medium | Skill metadata, per-run workspace isolation, provenance capture, and optional approval policy are now in place. | P0 |
| Final overview had no safety gate. | Unsafe intermediate content could reach publication draft. | High | Low | Final output can be withheld or warned based on classifier action. | P0 |
| Fetched web content can contain prompt injection. | Literature/tool outputs can manipulate agents. | Medium | Medium | Add adversarial fixtures for abstracts and markdown; keep using quote wrappers for untrusted text. | P1 |
| SSRF needs continuous regression coverage. | Local metadata or internal services could be fetched. | High | Low | Existing SSRF tests should remain required in CI. | P0 |
| Secrets are environment-scoped but skill env allowlist is broad. | Tool scripts can exfiltrate keys if malicious. | High | Medium | Move to per-skill required secret injection plus visible approval for risky skills. | P1 |

## Efficiency Roadmap

| Opportunity | Implementation | Metric |
| --- | --- | --- |
| Model routing | Keep expensive models for final synthesis, deep reflection, and top-ranked debates; use cheaper models for parse, first-pass review, duplicate detection, and low-Elo comparisons. | Tokens and USD per accepted top-10 hypothesis. |
| Prompt compression | Cache literature summaries and use structured review digests in ranking instead of full reviews. | Ranking prompt tokens per match. |
| Duplicate suppression | Run proximity/embedding clustering before full reflection and before tournament insertion. | Duplicate hypotheses reviewed per session. |
| Retrieval cache | Cache article metadata, abstracts, PDF text, and citation verification results by source hash. | Retrieval latency and external API calls. |
| Batched ranking | Batch low-priority pairwise comparisons where provider supports batch API. | Matches per dollar and queue throughput. |
| Resumable tool runs | Treat skill outputs as workspace artifacts with provenance. | Repeated analysis reruns avoided. |

## Augmented Scientist Workspace

The local researcher app should organize each session as a workspace with:

- `project_file`: user-uploaded papers, notes, and constraints.
- `retrieved_literature`: PubMed, Europe PMC, arXiv, OpenAlex, and local PDF extracts.
- `dataset`: uploaded or retrieved tabular/omics/assay data.
- `analysis`: executable skill outputs, notebooks, figures, and logs.
- `draft`: manuscript sections, claims tables, reviewer responses, and protocols.
- `citation`: citation graph records and claim-to-source mappings.
- `final_publication`: export-ready reports, manuscripts, and supplements.

The new `ScientistWorkspace` manifest provides a minimal local foundation for
these artifact types without replacing SQLite session state.

## Scientific Skill Interface

Each skill should expose metadata in `SKILL.md` front matter:

- `category`: retrieval, analysis, drafting, visualization, validation, or utility.
- `required_files`: user/project files the skill expects.
- `required_secrets`: exact environment variables the skill needs.
- `network_access`: whether the skill needs outbound network access.
- `write_scope`: `none`, `run_workspace`, `artifacts`, or a broader declared scope.
- `expected_outputs`: artifact types the run should produce.
- `safety_level`: trusted local, sensitive biomedical, dual-use review, or blocked.
- `requires_approval`: force visible approval before execution under approval policy.

The current local default remains `trusted_local` for fast experimentation, but
production or institutional pilots should switch to `approval_required`.

## Test and Benchmark Plan

- Paper-fidelity: assert agent follow-up scheduling from Supplement 8 and prompt sections from Supplement 9.
- Efficiency: report token/cost per agent, queue throughput, duplicate rate, retrieval latency, and matches per dollar.
- Safety: test unsafe goals, unsafe intermediate hypotheses, unsafe final reports, classifier outage behavior, malicious skill metadata, path traversal, SSRF, prompt-injected abstracts, oversized downloads, and secret leakage.
- Skill execution: test retrieval-only, analysis-producing-artifacts, drafting, failed, timed-out, approval-required, and resumable runs.
- Human workflow: create local project, upload PDFs/data, run a session, inspect ranked hypotheses, approve tools, generate analysis, and draft publication artifacts.

## Recommended Path

1. Harden P0 gates: goal safety, hypothesis safety, final overview safety, and skill provenance/approval.
2. Add workspace UI for artifacts and skill run history.
3. Implement retrieval cache and duplicate suppression before expensive reflection.
4. Add curated science-skill packs for retrieval, data analysis, visualization, and publication drafting.
5. Run AML-style regression presets and report quality/cost/safety deltas before broadening the tool surface.
