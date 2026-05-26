# Development log

This is the build history for the co-scientist codebase. Operational docs
live elsewhere:

- [`../README.md`](../README.md) — what it is + how to use it
- [`BENCH_RESULTS.md`](BENCH_RESULTS.md) — every bench ever run + per-candidate output

The system was built in 9 milestones (M0–M9), then extended with a
multi-provider LLM layer and a cross-model bench. Each milestone is
described below in terms of what landed, not what remains.

## Milestones

- **M0 — Skeleton.** Package layout, pydantic-settings config, SQLite schema + migrations (12 tables incl. `spans` / `events` / `elo_journal`), ULID + deterministic-hash IDs, structlog JSONL logging.

- **M1 — Storage, vectors, tools.** 10 repos; Voyage + OpenAI embedders; FAISS `IndexFlatIP` per-session store; built-in tools (`web_search`, `web_fetch`, `pubmed_search`, `arxiv_search`, `europe_pmc_search`); science-skills bridge that parses `SKILL.md` + shells out to scripts with a path-traversal guard.

- **M2 — Anthropic SDK layer.** 14 prompt templates; Jinja2 loader; retry honoring Retry-After for 429 / 529; `TokenBudget` with per-agent shares; model routing with never-degrade list; `AnthropicClient` with 4-tier `cache_control`, retry, transcript persistence, USD accounting; tool-loop driver that preserves thinking-block signatures and tracks URLs for citation honesty; `UNTRUSTED_SOURCE` quoting for prompt-injection defense.

- **M3 — Generation + Reflection.** `BaseAgent`; literature-strategy `GenerationAgent` with `record_hypothesis` tool, dedup via FAISS, hallucinated-URL filter; full-mode `ReflectionAgent` with `record_review` + URL filter.

- **M4 — Ranking + Elo tournament.** `AddToTournament` + `RunTournamentBatch` with pair selection weighted by `exp(-Δelo/200) · (1 - cosine_sim)`, debate-vs-pairwise mode switching, anchor-cached debates, idempotent `elo_journal` updates.

- **M5 — Supervisor scheduling.** Durable resume with lease reclaim + max-attempts dead-letter; hybrid termination (BUDGET / WALL_CLOCK / ELO_STABLE / EXTERNAL); `StabilityTracker` with snapshot history; `decide_next_steps` for idle refinement; pause / resume / abort via DB-flagged `session.status`. In-memory `EventBus` shared with the web UI.

- **M6 — Evolution + Proximity + Meta-review.** Evolution strategies (combine on most-distant top pair, simplify, feasibility, out_of_box) with `parent_ids`; Proximity batch recluster with sklearn agglomerative; periodic Meta-review system feedback (auto-injected into future Generation / Evolution prompts); final research overview synthesis.

- **M7 — Web UI.** FastAPI + Jinja2 + htmx + Pico.css + SSE. Pages: sessions index, new session form, session dashboard (live leaderboard, match feed, budget gauges), hypothesis detail with reviews, final overview. API endpoints for pause / resume / abort / feedback. `co-scientist serve` boots both the UI and a Supervisor in one process.

- **M8 — Safety + observability + evals.** Haiku-backed safety classifier with allow / warn / quarantine / block actions; citation verifier (fetch URL, check excerpt-substring); read-only `obs/metrics` (tokens, cost, cache hit ratio, P50 / P95 latency, dead tasks) backing `/api/sessions/{id}/metrics`; LLM-as-judge rubric runner with bundled fixtures; `co-scientist eval [agent] [--offline]`.

- **M9 — Batch API + estimator + resume hardening.** `BatchPool` for sub-decile tournament matches (50% cheaper Batch API submission with safe requeue on failure); pre-flight `estimator` that warns when projected USD spend > 1.2× budget; `co-scientist estimate` subcommand.

## Post-M9 work

- **Multi-vendor LLM layer.** Pluggable `LLMProvider` abstraction. First-class presets for OpenRouter, OpenAI, Gemini, Groq, Together, Mistral, Ollama. The Anthropic-only optimizations (cache breakpoints, extended thinking, batch) keep working under `provider = anthropic`; degrade gracefully elsewhere (cache markers stripped, thinking → `reasoning_effort` for o-series, batch unavailable).

- **`co-scientist bench` subcommand.** Cross-model Elo tournament with structured verdict tool call; gold-set scoring with AML drug-repurposing answer keys (5-drug and strict top-3 lists from the Co-Scientist paper); `pipeline` vs `direct` mode lets you A/B the full agent harness against a single raw LM call on the same goal. See [`BENCH_RESULTS.md`](BENCH_RESULTS.md) for live results.

## Testing

- 213 unit tests (`co_scientist/tests/unit/`).
- `pytest co_scientist/tests/unit -q` runs the full suite offline — Anthropic / OpenAI / embeddings / web tools are mocked or stubbed.
- `ruff check co_scientist` is part of the pre-commit gate.
- Live smoke tests against real LLM providers are **manual** and not run in CI.
