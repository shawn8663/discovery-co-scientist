# Discovery Co-Scientist

Discovery Co-Scientist is a local, multi-agent scientific discovery application
that combines two complementary architectures:

- A Google AI co-scientist-style hypothesis engine: Generation, Reflection,
  Ranking, Evolution, Proximity, and Meta-review agents coordinated by a
  durable Supervisor.
- A Robin-inspired therapeutic discovery workflow: assay proposal, assay
  evaluation, therapeutic candidate generation, candidate evaluation, bounded
  BTL ranking, controlled analysis artifacts, and experiment-informed
  regeneration.

This project builds on and substantially extends the original open-source
Co-Scientist project. It is an independent implementation and is not affiliated
with Google, FutureHouse, or the authors of the referenced papers.

See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for attribution and source
inspiration.

## Workflows

### General Hypothesis

The default workflow preserves the existing co-scientist behavior:

```bash
discovery-coscientist run "Identify hypotheses about microbiome-driven inflammation" \
  --workflow general_hypothesis \
  --n 3 \
  --budget-usd 2.0
```

The Supervisor parses the goal, schedules Generation, reviews hypotheses,
adds reviewed ideas to an Elo tournament, evolves high-ranking ideas, and
produces a final overview.

### Therapeutic Discovery

The Robin-style workflow focuses on lab-in-the-loop therapeutic discovery:

```bash
discovery-coscientist run \
  --workflow therapeutic_discovery \
  --disease "dry AMD" \
  --budget-usd 5.0
```

This schedules assay generation first, then assay evaluation/ranking,
therapeutic candidate generation, candidate evaluation/ranking, optional
analysis of uploaded experimental data, and experiment-informed regeneration.

Register and analyze experimental data:

```bash
discovery-coscientist analyze <session_id> \
  --kind flow_cytometry \
  --dataset /path/to/results.csv
```

The legacy `co-scientist` command remains as a compatibility alias for this
release.

## Architecture

```text
Scientist goal / disease
        |
        v
Supervisor + durable SQLite task queue
        |
        +-- general_hypothesis: Generation -> Reflection -> Ranking/Elo
        |                       -> Evolution -> Meta-review
        |
        +-- therapeutic_discovery: Assay -> Candidate -> Analysis
                                   -> Result interpretation -> Regeneration
```

Shared infrastructure includes:

- SQLite session/task state with migrations and idempotent queue inserts.
- Workspace manifests for project files, retrieved literature, datasets,
  analysis artifacts, citations, drafts, figures, and final outputs.
- Evidence bundles that prioritize uploaded project files, search Paperclip
  first when configured, broaden through OpenAlex, Europe PMC, PubMed,
  preprints, and ClinicalTrials.gov, and preserve both raw retrieval artifacts
  and deduplicated canonical evidence.
- FAISS/vector-based proximity and duplicate suppression for the general
  hypothesis workflow.
- Provider-selectable LLM routing across Anthropic, OpenAI, OpenRouter,
  Gemini, Groq, Together, Mistral, Ollama, and OpenAI-compatible endpoints.
- Safety gates for goals, hypotheses, final reports, and Robin-style
  therapeutic outputs.

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
```

Secrets are loaded from the environment. In this workspace, global Codex
instructions require loading keys from `~/.Codex/.env` before API calls.

Initialize local storage:

```bash
discovery-coscientist init
```

Preview evidence retrieval before a full run:

```bash
discovery-coscientist evidence \
  --prompt-file initial_prompt.txt \
  --project-dir /path/to/background_papers \
  --max-results-per-source 50 \
  --ranking-modes relevance,recent,impact
```

Run the web UI:

```bash
discovery-coscientist serve
```

The Runs dashboard at `http://localhost:7878/runs` lists active and past sessions.
Each row links to a per-session dashboard for live monitoring and historical review.

## Benchmarks

The original AML/general-hypothesis benchmark remains available through
`discovery-coscientist bench --preset paper-aml`. Discovery-specific offline
benchmark fixtures are registered in `co_scientist.bench.fixtures`, including
`robin-dry-amd` for therapeutic discovery and `aml-general-compatibility` for
old-workflow regression checks. Fixture metrics cover quality, cost, latency,
duplicate rate, retrieval hits, and ranking agreement.

## Documentation

- [docs/CO_SCIENTIST_APPLICATION_MANUAL.md](docs/CO_SCIENTIST_APPLICATION_MANUAL.md)
- [docs/EVIDENCE_RETRIEVAL.md](docs/EVIDENCE_RETRIEVAL.md)
- [docs/ROBIN_WORKFLOW.md](docs/ROBIN_WORKFLOW.md)
- [docs/NEW_REPO_MIGRATION.md](docs/NEW_REPO_MIGRATION.md)
- [docs/BENCH_RESULTS.md](docs/BENCH_RESULTS.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
