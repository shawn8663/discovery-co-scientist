# Evidence Retrieval

Discovery Co-Scientist builds an evidence bundle before generation. This makes
source coverage, deduplication, ranking, and provenance inspectable before the
application spends model budget on hypothesis or therapeutic synthesis.

The `discovery-coscientist evidence` command runs the same first-stage evidence
workflow without kicking off Generation, Reflection, Ranking, or Robin candidate
steps. It is the preferred way to test a prompt, project folder, and retrieval
settings before a full run.

## Source Priority

The evidence planner searches sources in a deliberate order:

1. Uploaded PDFs, markdown, text, and project files.
2. Paperclip, when enabled and authenticated.
3. OpenAlex.
4. Europe PMC.
5. PubMed.
6. arXiv and a Europe PMC bioRxiv/medRxiv preprint pass.
7. ClinicalTrials.gov for therapeutic or translational goals.

Local material is always first because user-provided background should anchor
the run and be deduplicated against external sources. Paperclip is the first
external source. OpenAlex and Europe PMC broaden coverage next, followed by
PubMed, arXiv/preprints, and clinical trials when the parsed goal or selected
workflow is clinical or translational. The `therapeutic_discovery` workflow is
always treated as clinical/translational for evidence planning.

## Retrieval Lanes

The default retrieval lanes are `relevance`, `recent`, and `impact`. Lanes are
source-aware, so each source receives only the lane options it can support.

| Source | Relevance | Recent | Impact |
| --- | --- | --- | --- |
| Uploaded project files | yes | no | no |
| Paperclip | yes | yes | no |
| OpenAlex | yes | yes | yes, by citation count |
| Europe PMC | yes | yes | no |
| PubMed | yes | yes | no |
| arXiv | yes | yes | no |
| bioRxiv/medRxiv pass | yes/recent pass | yes/recent pass | no |
| ClinicalTrials.gov | yes | no | no |

## Defaults

The default `[evidence_retrieval]` configuration is:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `depth` | `balanced` | Named retrieval depth profile. |
| `default_limit` | `25` | Fallback per-source result limit. |
| `local_limit` | `20` | Uploaded project-file search limit. |
| `paperclip_limit` | `50` | Paperclip search limit. |
| `openalex_limit` | `25` | OpenAlex search limit. |
| `pubmed_limit` | `25` | PubMed search limit. |
| `europe_pmc_limit` | `25` | Europe PMC search limit. |
| `arxiv_limit` | `15` | arXiv search limit. |
| `preprint_limit` | `15` | bioRxiv/medRxiv preprint pass limit. |
| `clinical_trials_limit` | `25` | ClinicalTrials.gov search limit. |
| `ranking_modes` | `relevance,recent,impact` | Retrieval lanes to plan. |
| `retain_raw_results` | `true` | Keep raw search result artifacts. |
| `deduplicate_canonical_evidence` | `true` | Build a deduplicated canonical index. |
| `max_canonical_items` | `200` | Maximum canonical records stored in the bundle. |
| `group_limit` | `25` | Maximum canonical IDs per review group. |
| `relevance_weight` | `0.45` | Ranking score relevance weight. |
| `impact_weight` | `0.25` | Ranking score citation/impact weight. |
| `recency_weight` | `0.20` | Ranking score recency weight. |
| `corroboration_weight` | `0.10` | Ranking score multi-source support weight. |

Passing `--max-results-per-source` updates the source limits for that command:
local files are capped at 20, Paperclip at 1000, and the other external sources
at 50. Passing `--ranking-modes` replaces the configured lane list.

## Raw and Canonical Evidence

Raw retrieval outputs are retained as workspace artifacts of kind
`retrieved_literature` under:

```text
data/workspaces/<session_id>/retrieved_literature/
```

These artifacts preserve original source responses, queries, tools, lanes,
cache metadata, and citation metadata where available.

The evidence bundle is a workspace artifact of kind `evidence_bundle` under:

```text
data/workspaces/<session_id>/evidence/
```

The bundle contains the planned searches, source accounting, execution status,
and the deduplicated `canonical_evidence` index. Canonical items are merged by
DOI, PMID, arXiv ID, URL, and normalized title. Each canonical item keeps its
source-hit provenance so downstream reviews and reports can trace a claim back
to the exact retrieved records that supported it. Raw complete result payloads
are not embedded in the bundle; they remain in the separate
`retrieved_literature` artifacts.

Important bundle fields include:

| Field | Meaning |
| --- | --- |
| `planned_searches` | Ordered search plan with source, tool, query, lane, and enabled status. |
| `source_accounting` | Counts and status by source. |
| `executed_searches` | Search outcomes and artifact references. |
| `canonical_evidence` | Deduplicated, ranked evidence records. |
| `evidence_groups` | Review group IDs such as newest or highest impact. |
| `deduplication_keys` | DOI, PMID, arXiv, URL, or title keys used to merge records. |

## Ranking and Groups

Canonical evidence is ranked with transparent score components:

- Relevance to the research prompt or hypothesis.
- Citation or impact signal when the source provides it.
- Recency.
- Corroboration across multiple source hits.

The bundle exposes evidence groups for common review views:

- `highest_relevance`
- `newest`
- `highest_impact`
- `preprints`
- `clinical_translational`

## CLI Usage

Create an evidence bundle with parsed-goal query planning:

```bash
discovery-coscientist evidence \
  --prompt-file initial_prompt.txt \
  --project-dir /path/to/background_papers \
  --max-results-per-source 50 \
  --ranking-modes relevance,recent,impact
```

Use `--no-parse-goal` for a no-LLM preview that uses the prompt text directly
as the retrieval query:

```bash
discovery-coscientist evidence \
  --prompt-file initial_prompt.txt \
  --project-dir /path/to/background_papers \
  --no-parse-goal
```

Prompt files may also include retrieval settings:

```text
retrieval_settings:
  max_results_per_source: 50
  ranking_modes: relevance,recent,impact
```

Prompt-file retrieval settings are applied first. Explicit CLI flags override
the prompt-file settings for that command invocation.

## Paperclip Setup

Paperclip retrieval is optional. Install the extra and authenticate the
Paperclip CLI/SDK before using it:

```bash
source ~/.Codex/.env
uv sync --extra dev --extra paperclip
paperclip login
```

Then enable the tools:

```toml
[paperclip]
enabled = true
```

The current evidence planner also checks that `PAPERCLIP_API_KEY` is non-empty
before marking Paperclip as enabled in the source plan. The `gxl-paperclip` SDK
execution path itself uses the OAuth credentials created by `paperclip login`,
not the API key value.
