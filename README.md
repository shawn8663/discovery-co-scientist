# AI Co-Scientist

A multi-agent system for tournament-style scientific hypothesis generation, ranking, and synthesis. Built on the architecture described in [`reference/`](reference/) (Google's Co-Scientist), implemented in Python on top of pluggable LLM provider SDKs.

The system takes a natural-language research goal, runs six specialized LLM agents in a coordinated loop, and produces a *Research Overview* of the top-ranked hypotheses:

- **Generation** — proposes hypotheses via literature review and simulated scientific debate
- **Reflection** — reviews hypotheses for novelty, correctness, and testability; deep-verifies assumptions
- **Ranking** — runs an Elo tournament with simulated debates between hypotheses
- **Evolution** — combines, simplifies, and reimagines top-ranked hypotheses
- **Proximity** — embeds and clusters hypotheses to drive dedup and informative pairings
- **Meta-review** — synthesizes system-wide feedback and the final research overview

A **Supervisor** schedules agents via a durable task queue (SQLite-backed) with bounded concurrency.

> **Other docs**
> - [`docs/BENCH_RESULTS.md`](docs/BENCH_RESULTS.md) — every cross-model bench ever run on this code, with per-candidate Elo, every hypothesis produced, gold-set hits, and direct file pointers. Auto-generated from the bench DB.
> - [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — milestone-by-milestone build history.

## Contents

- [Architecture](#architecture)
- [Install](#install)
- [Initialize](#initialize)
- [Run a research session](#run-a-research-session)
- [LLM provider](#llm-provider)
- [Configuration](#configuration)
- [Bench: compare models head-to-head](#bench-compare-models-head-to-head)
- [Repository layout](#repository-layout)

## Architecture

```
                       co-scientist run "<goal>"
                                  │
                                  ▼
            ┌──────────────────────────────────────┐
            │            Supervisor                │  durable task queue (SQLite)
            │  • parse_goal → ResearchPlan         │  bounded concurrency
            │  • enqueue initial Generation tasks  │  lease + dead-letter + resume
            │  • main loop: claim → run → follow-up│  termination: BUDGET / WALL_CLOCK
            │  • _decide_next_steps when idle      │              / ELO_STABLE / IDLE / EXTERNAL
            │  • _finalize: meta-review final      │
            └──────────────────────────────────────┘
                                  │  tasks
            ┌─────────────────────┼─────────────────────────────┐
            ▼                     ▼                             ▼
   ┌──────────────┐      ┌──────────────┐              ┌──────────────┐
   │  Generation  │      │  Reflection  │              │   Ranking    │
   │  literature  │      │  full / verif│              │ pairwise vs  │
   │  +tool loop  │─►hyp│  +URL check  │─►review─►rank│   debate     │──►Elo
   └──────────────┘      └──────────────┘              └──────────────┘
            ▲                                                   │
            │                                                   ▼
   ┌──────────────┐      ┌──────────────┐              ┌──────────────┐
   │  Evolution   │◄─────│ Meta-review  │              │  Proximity   │
   │ combine /    │ feed │ system fdbk  │              │ FAISS recluster│
   │ simplify /   │ back │ final overview│             │ dedup + close│
   │ out_of_box   │      └──────────────┘              │ Elo pairings │
   └──────────────┘                                    └──────────────┘
            │
            ▼
       new hypotheses re-enter the cycle


  Shared infrastructure
  ─────────────────────
  • LLMProvider  ─ anthropic / openai / openrouter / gemini / groq /
                   together / mistral / ollama / openai_compatible
  • ToolRegistry ─ web_fetch + pubmed/arxiv/europe_pmc;
                   web_search auto-registered iff TAVILY/BRAVE key set;
                   science-skills via SKILL.md frontmatter
  • TokenBudget  ─ per-agent shares + global cap; reservation released on retry
  • EventBus     ─ in-memory fan-out to SSE for the live web UI
  • FaissStore   ─ IndexFlatIP, asyncio-locked, atomic save/load;
                   Voyage → OpenAI → hash-fallback embedder chain
  • SQLite       ─ 15 tables incl. sessions / hypotheses / reviews / tasks /
                   tournament_matches / transcripts / events / bench_*
                   (WAL, busy_timeout, schema_migrations idempotent runner)
```

## Install

```bash
# Recommended: Python 3.11–3.13 (FAISS wheel availability)
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# fill in the API key for whichever LLM provider you'll use (see below).
```

## Initialize

```bash
co-scientist init
co-scientist list
```

`init` creates `data/` (artifacts, vectors, logs) and applies migrations to `data/co_scientist.db`. The output prints which LLM provider it sees configured and whether its API key is set.

## Run a research session

```bash
co-scientist run "Identify hypotheses about microbiome-driven inflammation" \
  --n 3 --budget-usd 2.0 --wall-clock 600
```

This kicks off Generation → Reflection → Ranking → Evolution → Meta-review under the configured LLM provider. The Supervisor schedules tasks, the Elo tournament refines a leaderboard, and the final research overview is written to `data/artifacts/<session_id>/final/overview.md`.

```bash
co-scientist serve            # FastAPI + htmx + SSE dashboard at localhost:7878
co-scientist report <id>      # print the final overview
co-scientist status <id>      # session metadata + counts
co-scientist pause <id> | resume <id> | abort <id>
co-scientist feedback <id> --kind directive --text "focus on metabolic pathways"
```

## LLM provider

The agents talk to one LLM provider per session, configured in [`config/default.toml`](config/default.toml) (override with your own `co-scientist.toml`):

```toml
[llm]
provider = "anthropic"
```

| provider              | Endpoint                                                | Required key            | Example models                                            |
| --------------------- | ------------------------------------------------------- | ----------------------- | --------------------------------------------------------- |
| `anthropic` *(default)* | api.anthropic.com                                     | `ANTHROPIC_API_KEY`     | `claude-opus-4-7`, `claude-sonnet-4-6`                    |
| `openai`              | api.openai.com                                          | `OPENAI_API_KEY`        | `gpt-5`, `gpt-4o`, `o3-mini`                              |
| `openrouter`          | openrouter.ai — 200+ models from every major vendor     | `OPENROUTER_API_KEY`    | `anthropic/claude-3.5-sonnet`, `openai/gpt-5`, `google/gemini-2.5-pro`, `meta-llama/llama-3.3-70b-instruct` |
| `gemini` / `google`   | generativelanguage.googleapis.com (OpenAI-compat)       | `GEMINI_API_KEY`        | `gemini-2.5-pro`, `gemini-2.5-flash`                      |
| `groq`                | api.groq.com                                            | `GROQ_API_KEY`          | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`           |
| `together`            | api.together.xyz                                        | `TOGETHER_API_KEY`      | `meta-llama/Llama-3.3-70B-Instruct-Turbo`                 |
| `mistral`             | api.mistral.ai                                          | `MISTRAL_API_KEY`       | `mistral-large-latest`, `codestral-latest`                |
| `ollama`              | localhost:11434 — local models                          | *(none)*                | `llama3.3:70b`, `qwen2.5:32b`                             |
| `openai_compatible`   | Anything else; set `[llm.openai] base_url` explicitly   | `OPENAI_API_KEY`        | depends                                                   |

Mixing vendors per session requires picking the provider once; for multi-vendor routing in a single session, use `provider = "openrouter"` and let OpenRouter dispatch upstream per model:

```toml
[llm]
provider = "openrouter"
[llm.openrouter]
referer = "https://your-app.example.com"   # optional, for catalog attribution
title   = "My Co-Scientist"

[models]
generation         = "anthropic/claude-3.5-sonnet"
reflection         = "openai/gpt-5"
ranking_pairwise   = "google/gemini-2.5-flash"
metareview_final   = "anthropic/claude-opus-4-7"
```

Cost is estimated via `co_scientist/llm/routing.py`'s `PRICE_TABLE`; unknown models match a family-hint (flash / mini / opus / sonnet / gemini / llama / mistral) so brand-new previews price sensibly. Tighten `[run] budget_usd` if running on a new model you haven't sanity-checked.

**Provider feature support:**

| Feature              | anthropic | openai (o-series) | openai (gpt) | openai_compatible |
| -------------------- | --------- | ----------------- | ------------ | ----------------- |
| Tool / function call | ✅        | ✅                | ✅           | depends on endpoint |
| Extended reasoning   | ✅ (thinking) | ✅ (`reasoning_effort`) | ❌ (dropped) | endpoint-specific |
| Prompt-cache breakpoints | ✅    | ❌                | ❌           | ❌                |
| Batch API (50%-off ranking) | ✅ | ❌            | ❌           | ❌                |

## Configuration

Layered: [`config/default.toml`](config/default.toml) → `~/.co-scientist/config.toml` → `./co-scientist.toml` → `--config <path>`. Secrets come from environment only (see [`.env.example`](.env.example)).

## Bench: compare models head-to-head

`co-scientist bench` runs the same goal under N different `(provider, model)` configurations and ranks them via a single shared Elo tournament. Each candidate independently generates hypotheses; then every candidate-pair plays `--matches` head-to-head debates, judged by ONE fixed judge model (picked separately so no candidate scores its own work).

> **For live numbers** — per-candidate Elo, the actual hypotheses each model proposed, gold-set hits, and what the data showed — see [`docs/BENCH_RESULTS.md`](docs/BENCH_RESULTS.md). It includes a headline-findings section at the top so you don't have to scroll through every bench.

### Presets

| `--preset`               | What it does |
| ---                      | --- |
| `paper`                  | Co-Scientist paper baselines (Gemini 2 Flash Thinking, Gemini 2 Pro, OpenAI o1, Claude Haiku) via OpenRouter, head-to-head Elo only |
| `paper-aml`              | Same candidates + the paper's AML drug-repurposing goal + gold-set recall scoring (defaults to the strict top-3 set: Nanvuranlat / KIRA6 / Leflunomide) |
| `paper-aml-vs-raw`       | `paper-aml` but each model runs **both** in the full pipeline AND as a single raw LM call — isolates the multi-agent harness's value-add |
| `frontier-aml-vs-raw`    | Same pipeline-vs-raw setup but with current frontier models (Claude Opus 4.7, GPT-5, Gemini 3 Pro / Flash) |

```bash
# Reproduce the paper preference-ranking comparison:
co-scientist bench --preset paper --budget-per-candidate 1.5

# Score against the paper's AML drug picks:
co-scientist bench --preset paper-aml --n 3 --matches 2

# Compare multi-agent pipeline vs raw model call on the same goal:
co-scientist bench --preset paper-aml-vs-raw --n 1 --budget-per-candidate 1.5

# Current frontier models, pipeline vs raw:
co-scientist bench --preset frontier-aml-vs-raw --n 1 --budget-per-candidate 2.0
```

### Pipeline vs raw LM (one model, isolated)

The `--preset *-vs-raw` presets pit each model's **full co-scientist Generation pipeline** (literature tools + tool loop + dedup + `record_hypothesis`) against a **single raw LM call** with the same model + a forced `record_hypothesis` function call (no tools). Lets you measure how much of the system's output quality comes from the multi-agent harness vs the underlying model. → live numbers in [`docs/BENCH_RESULTS.md`](docs/BENCH_RESULTS.md#headline-findings).

### Gold-set scoring (AML drug repurposing)

`paper-aml*` presets score **recall** against a curated answer key from the Co-Scientist paper. Two gold sets ship; both stay registered so historical bench artifacts remain interpretable.

| label                                                   | size | what it is |
| ---                                                     | --- | --- |
| `aml-repurposing-paper-top3` *(default for `paper-aml*`)* | 3 | Top-3 of the paper's *ranked* list under the **strict methodology**: candidates with no prior published AML repurposing, no prior preclinical evidence in AML, and no external inputs (no DepMap scores, no expert curation). → **Nanvuranlat (JPH-203 / KYT-0353), KIRA6, Leflunomide (Arava / HWA-486 / Teriflunomide / Aubagio)** |
| `aml-repurposing-paper-5`                               | 5 | Broader 5-drug list referenced in the paper's main text. Includes well-known candidates, some with prior preclinical AML evidence. → **Binimetinib (MEK162), Pacritinib (SB1518 / Vonjo), Cerivastatin (Baycol), Pravastatin (Pravachol), Dimethyl fumarate (DMF / BG-12 / Tecfidera)** |

Swap with `--goldset`:

```bash
co-scientist bench --preset paper-aml --goldset aml-repurposing-paper-5   # broader list
co-scientist bench --preset paper-aml --goldset none                       # head-to-head only
```

The matcher is whole-token, case-insensitive, and looks at every searched field of every hypothesis (title / summary / full_text / `entities` / citation excerpts). Drug **class** mentions (e.g. "DHODH inhibitor") do **not** count — the candidate has to name the actual compound (or one of its registered aliases).

### Custom candidates

`label=provider:model[@mode]`. `mode` is `pipeline` (default) or `direct`. Pipeline goes through the full Generation agent stack; direct is a single forced-tool LM call with no literature tools.

```bash
co-scientist bench "Identify hypotheses about X" \
  -c flash3=openrouter:google/gemini-3-flash-preview \
  -c flash3-raw=openrouter:google/gemini-3-flash-preview@direct \
  -c gpt5=openai:gpt-5 \
  -c opus=anthropic:claude-opus-4.7 \
  --judge anthropic:claude-sonnet-4-6
```

### Where results live

Every bench writes to SQLite + JSON on disk:

```
data/co_scientist.db                          ← SQLite, all metadata
  bench_runs                                  one row per bench
  bench_candidates                            one row per (bench × candidate × mode)
  bench_matches                               one row per head-to-head

data/artifacts/<session_id>/                  ← JSON on disk
  bench/<bench_id>.json                       run summary + per-entity gold_hit_detail
  hypotheses/<hyp_id>.json                    every hypothesis the bench produced
  transcripts/generation/<trn_id>.json        every LLM call
```

The auto-generated [`docs/BENCH_RESULTS.md`](docs/BENCH_RESULTS.md) (rebuild with `python scripts/build_bench_report.py`) walks every recorded bench and renders the per-candidate result table, every hypothesis attributed to the model that produced it, and a post-hoc rescore against every registered gold set.

### Mechanics

- **Generation runs in parallel** per candidate under a deep-copied Config (`cfg.llm.provider`, `cfg.models.*`, thinking budgets zeroed for non-Anthropic).
- **Round-robin pairings**: every pair plays `--matches` head-to-heads (one random hypothesis from each side per match).
- **Structured verdict** via a forced `record_verdict` function call — no fragile `better idea: <N>` text parsing across providers.
- Bench runs are **isolated from regular sessions** — they don't write to `tournament_matches` or affect any session's leaderboard.

## Repository layout

```
co_scientist/
  agents/       # supervisor + 6 specialized agents
  bench/        # cross-model bench runner (compare via Elo tournament)
  llm/          # provider abstraction (anthropic/openai/openrouter/gemini/...),
                # tool loop, budgets, routing, retry
  storage/      # SQLite schema + migrations, db connection, 15 repos
  tools/        # tool registry; web/search, science-skills, code exec
  vectors/      # embeddings (Voyage/OpenAI/hash-fallback) + FAISS index
  orchestrator/ # task queue, worker pool, termination, event bus
  safety/       # injection quoting, classifier, citation verifier
  obs/          # spans, metrics
  web/          # FastAPI + htmx + SSE UI + sanitized markdown renderer
  evals/        # per-agent + e2e + regression evals
  tests/        # 213 unit tests + fixtures + smoke
config/
  default.toml
  prompts/      # Jinja2 templates per agent.mode
docs/
  BENCH_RESULTS.md   # every bench ever run (auto-generated)
  DEVELOPMENT.md     # milestone-by-milestone build history
scripts/
  build_bench_report.py
reference/      # input materials (pseudocode, prompts, diagrams)
data/           # gitignored; runtime artifacts
vendor/         # gitignored; pinned clone of google-deepmind/science-skills
```

## License

Apache-2.0.
