# Runbooks

Runbooks let an approved workflow document launch multiple Discovery Co-Scientist
runs in order, print dashboard links, and write a final Markdown summary.

Use TOML so the format matches the rest of the local configuration.

```toml
title = "Immune surfaceome aptamer workflow"
approved = true
stop_on_failed_step = true

[summary]
output = "runbook-summary.md"

[defaults]
workflow = "general_hypothesis"
n = 15
wall_clock = 7200
budget_usd = 60
preferences_file = "preferences.txt"
project_file = ["background.pdf"]

[[steps]]
name = "baseline_surfaceome_strategy"
prompt_file = "prompts/01-baseline.md"

[[steps]]
name = "reagent_gap_prioritization"
prompt_file = "prompts/02-reagent-gap.md"
n = 10
```

Validate without starting runs:

```bash
uv run discovery-coscientist runbook execute workflow.toml --dry-run
```

Execute approved steps:

```bash
source ~/.Codex/.env
uv run discovery-coscientist runbook execute workflow.toml
```

The executor runs steps sequentially through the same Supervisor used by
`discovery-coscientist run`. Each step inherits `[defaults]`, can override run
settings, and appears in the normal dashboard.
