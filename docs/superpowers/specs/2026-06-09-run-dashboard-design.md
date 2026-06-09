# Run Dashboard Design

## Purpose

Create a dashboard experience that makes Discovery Co-Scientist runs easier to monitor while active and easier to review after completion. The dashboard should expose the run as a set of parallel phase panels, with run health given first visual priority and scientific results organized for later inspection.

## Goals

- Provide a canonical entry point for finding current and past runs without knowing a full session ID.
- Give each run a stable dashboard URL that works for running, paused, done, failed, and aborted sessions.
- Show live operational health during a run: current tasks, queue status, failures, retries, and latest activity.
- Summarize phase progress for prompt development, evidence, generation, review, tournament/ranking, outputs, and therapeutic discovery workflows.
- Support completed-run review through the same phase panels instead of requiring a separate report-only page.
- Reuse existing SQLite tables, workspace artifacts, htmx, and SSE infrastructure.

## Non-Goals

- Do not create a new metrics store for the first version.
- Do not add a frontend build system.
- Do not require users to know or paste a complete session ID to find a dashboard.
- Do not implement advanced cross-run analytics in the first version.

## Navigation

The dashboard has two levels.

### Global Runs Index

The global entry point is `/runs`, with `/` either redirecting to it or rendering the same page. This page is the default place to open between runs.

It lists all sessions from newest to oldest, with active runs pinned at the top. Each run row shows:

- Short session ID, backed by links using the full session ID.
- Status.
- Workflow.
- Research goal.
- Updated time.
- Budget used.
- Run-health summary.
- Scientific progress summary.
- Final overview availability.
- Links to the per-run dashboard and final overview when available.

The page should query SQLite on load so it is current after restarts. A manual refresh is enough for the first version; light polling for active runs can be added later.

### Per-Session Dashboard

Each session has a stable URL:

```text
/sessions/{session_id}/dashboard
```

The dashboard works for active and historical sessions. Running sessions use live updates. Completed, failed, aborted, and paused sessions render as review dashboards and do not poll by default.

The existing session detail page may remain as a more detailed legacy view, or it can gradually become the dashboard once the new page is mature.

### CLI Links

The terminal should print dashboard access information for CLI-created runs.

For a new run, print:

```text
Runs dashboard: http://localhost:8000/runs
This run:       http://localhost:8000/sessions/{session_id}/dashboard
```

If the web server is not known to be running, also print:

```text
Start dashboard server: discovery-coscientist serve
```

The current CLI prints the session ID only after `Supervisor.run_session()` returns. To print a per-run link at start time, implementation should expose the session ID immediately after session creation, before the long-running supervisor loop completes. If that is not ready in the first implementation slice, the CLI should at least print the global `/runs` link before the run starts and the per-session link after completion.

Web-created runs should redirect to the per-session dashboard after creation. If needed, adjust session creation so the session row is inserted before the background worker starts.

## Dashboard Layout

The per-session dashboard is a balanced command center.

### Top Row

Run health receives first visual priority.

The run-health panel shows:

- Session status.
- Active task count.
- Pending task count.
- Done task count.
- Failed/dead task count.
- Retry count or attempted failures.
- Current active task agent/action.
- Latest event age.
- Pause, resume, and abort controls.

Compact summary panels sit beside run health:

- Budget and time: cost used, budget limit, token usage, wall-clock remaining or elapsed time, call count, latency summary.
- Scientific progress: evidence source count, hypotheses or candidates generated, reviews or evaluations completed, tournament matches or rankings completed, final output readiness.

### Phase Panels

Below the top row, parallel phase panels summarize the run. Each panel has a compact state and an obvious drill-down link or expandable detail area.

#### Prompt And Plan

Data:

- Parsed objective.
- Preferences.
- Constraints.
- Idea attributes.
- Domain hint.
- Retrieval queries.
- Clinical or translational flag.

Signals:

- Whether the parsed plan matches user intent.
- Number and quality of generated retrieval queries.
- Constraints or preferences that may affect later interpretation.
- Parse-goal transcript metadata when available.

#### Evidence

Data:

- Evidence bundle artifact.
- Local evidence sources.
- Planned searches.
- Source accounting.
- Canonical evidence records.
- Evidence groups.
- Retrieval tool-call events.

Signals:

- Local versus external source coverage.
- Enabled, disabled, executed, and failed searches.
- Source hit counts by provider.
- Retrieval cache hit ratio.
- Retrieval latency.
- Top canonical sources.
- Evidence-only preview status when no generation tasks were enqueued.

#### Generation

Data:

- Hypotheses table for general workflow.
- Therapeutic candidates for therapeutic workflow.
- Creation strategy.
- Parent IDs.
- Artifact paths.
- Duplicate suppression events.
- Deduplication clusters.

Signals:

- Ideas generated over time.
- Strategy mix.
- Newest hypotheses or candidates.
- Duplicate rate.
- Retired or quarantined outputs.
- Links to generated artifacts.

#### Review And Evaluation

Data:

- Hypothesis reviews.
- Assay evaluations.
- Candidate evaluations.
- Review/evaluation task backlog.

Signals:

- Screen and full review counts.
- Pass or promising rate.
- Verdict distribution.
- Novelty, correctness, testability, and feasibility score summaries.
- Review backlog and current review task.
- Weak or high-risk outputs needing attention.

#### Tournament And Ranking

Data:

- Tournament matches.
- Elo before and after values.
- Winners and invalid matches.
- Ranking trace events.
- Ranked assays and candidates.

Signals:

- Leaderboard.
- Match count.
- Invalid match rate.
- Elo movement.
- Top ideas stabilizing.
- Ranking latency and cost.
- Matches per dollar.

#### Evolution And Proximity

Data:

- Evolution tasks and generated hypotheses.
- Evolution strategy.
- Parent IDs.
- Proximity graph tasks.
- Embeddings metadata.
- Duplicate cluster state.

Signals:

- Which top ideas were evolved.
- Child ideas by strategy.
- Whether evolved ideas improve reviews or ranking.
- Duplicate clusters that still reached tournament.
- Proximity rebuild activity.

#### Outputs, Feedback, And Artifacts

Data:

- System feedback rows.
- Human feedback rows.
- Final overview path.
- Safety status for the overview.
- Workspace manifest artifacts.
- Recent events.

Signals:

- Human directives, preferences, pins, and rejections.
- Meta-review feedback.
- Final overview readiness.
- Safety warning or withheld status.
- Newest artifacts.
- Artifact counts by kind.

#### Therapeutic Discovery Additions

For `therapeutic_discovery` sessions, the dashboard should show workflow-specific panels or panel variants:

- Assays: proposal count, evaluation count, ranking score, pinned/rejected state.
- Candidates: candidate count, evaluation count, ranking score, pinned/rejected state.
- Analysis runs: dataset artifacts, analysis kind, trajectories, summary.
- Experiment insights: positive hits, negative hits, suggested mechanisms, follow-up assays.
- Regeneration: candidate generation rounds influenced by experiment insights.

These should not be forced into hypothesis-only language.

## Data Flow

The dashboard should use read-only aggregation over existing state:

- `sessions`
- `tasks`
- `events`
- `transcripts`
- `hypotheses`
- `reviews`
- `tournament_matches`
- `system_feedback`
- `assay_proposals`
- `assay_evaluations`
- `therapeutic_candidates`
- `therapeutic_candidate_evaluations`
- `analysis_runs`
- `experiment_insights`
- workspace manifests and artifacts

Create a dashboard summary helper that returns a structured view model:

```text
run_health
budget_time
scientific_progress
phase_panels
recent_events
links
```

This helper can start in `co_scientist/web/app.py` if small, but should move to `co_scientist/web/dashboard.py` if it grows. The same summary shape can back both the HTML template and a JSON refresh endpoint.

The existing `co_scientist.obs.metrics.session_metrics_cached()` should be reused for metrics already implemented, including cost, tokens, latency, duplicate rates, retrieval stats, ranking stats, and dead tasks.

## Refresh Model

Use mixed updates during active runs:

- SSE immediately appends live events.
- Run health polls every 1 to 2 seconds while the session is running.
- Budget/time and phase summaries poll every 5 to 10 seconds while running.
- Artifacts can refresh every 15 to 30 seconds or after artifact-related events if added.
- Completed, failed, aborted, and paused sessions do not poll by default.
- The global runs index refreshes on page load for the first version; active-row polling can be added later.

This keeps the top health panel responsive without hammering SQLite.

## Error Handling And Edge Cases

- No runs exist: show an empty state with a New Session action.
- Session row exists but no tasks exist yet: render the dashboard from the session row.
- Running session has no recent events: show "no recent events" and avoid implying failure.
- Final overview is missing: show "overview unavailable" instead of failing.
- Failed or dead tasks exist: surface them prominently in run health.
- Evidence-only preview run: show evidence panel and mark generation/tournament as not applicable.
- Therapeutic discovery run: show assays, candidates, analysis, and experiment insights.
- Missing artifact files: show artifact metadata and mark file unavailable.
- Unknown workflow or status: render generic panels and avoid hard failure.

## Testing

Add focused tests for the dashboard summary and routes.

Unit tests:

- Dashboard summary aggregates run-health task counts.
- Dashboard summary handles running sessions with no tasks.
- Dashboard summary handles done sessions with a final overview.
- Dashboard summary handles missing final overview.
- Evidence-only sessions render evidence progress and not-applicable generation/tournament state.
- Therapeutic discovery sessions include assay/candidate/analysis panels.

Web route tests:

- `/runs` lists active and past runs.
- `/` reaches the run index or renders equivalent content.
- Per-session dashboard renders for running, done, failed, and aborted sessions.
- Short IDs display while links use full session IDs.
- Final overview links appear only when available.

Existing unit-style tests should be enough for the first version. Browser automation is optional for later visual polish.

## Implementation Notes

- Keep the first version in the current FastAPI, Jinja, htmx, and SSE stack.
- Avoid schema changes unless a specific missing signal cannot be derived from existing tables.
- Prefer indexed aggregate queries and cached metrics helpers.
- Keep panel labels domain-aware: hypothesis language for `general_hypothesis`, assay/candidate language for `therapeutic_discovery`.
- Treat `/runs` as the canonical review hub and `/sessions/{session_id}/dashboard` as the drill-down.
