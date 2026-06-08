# Evidence Retrieval

Discovery Co-Scientist builds evidence bundles before generation. Evidence
retrieval is optimized separately from hypothesis generation so source coverage,
deduplication, and ranking can be inspected before spending model budget on
downstream synthesis.

## Source Order

The evidence planner searches sources in a deliberate order:

1. Uploaded PDFs and project files
2. Paperclip, when configured and authenticated
3. OpenAlex
4. Europe PMC
5. PubMed
6. arXiv and other preprint sources
7. ClinicalTrials.gov for therapeutic or translational goals

Local and project files are searched first so user-supplied context can anchor
the bundle. Paperclip, OpenAlex, Europe PMC, PubMed, preprint indexes, and
ClinicalTrials.gov then broaden coverage across literature, metadata, and
translational evidence.

## Retrieval Lanes

The default retrieval lanes are `relevance`, `recent`, and `impact`. Lanes are
source-aware: each source receives the lane options it can support. For example,
OpenAlex can emphasize citation count for impact, PubMed can sort by publication
date for recent work, Paperclip can search by relevance or date, and Europe PMC
supports relevance and recent retrieval.

## Raw and Canonical Evidence

Raw retrieval outputs are retained as workspace artifacts of kind
`retrieved_literature`. These artifacts preserve the original source responses,
queries, tools, lanes, and cache metadata for auditability.

The evidence bundle also includes `canonical_evidence`, a deduplicated evidence
index. Canonical items are merged by DOI, PMID, arXiv ID, URL, and normalized
title. Each canonical item preserves its source hits, so downstream reports can
trace a claim back to the exact source records that supported it.

## Ranking and Groups

Canonical evidence is ranked with transparent score components:

- Relevance to the research prompt or hypothesis
- Citation or impact signal when the source provides it
- Recency
- Corroboration across multiple source hits

The bundle exposes evidence groups for common review views:

- `highest_relevance`
- `newest`
- `highest_impact`
- `preprints`
- `clinical_translational`

## CLI Usage

Use `--max-results-per-source` to control source depth and `--ranking-modes` to
choose the retrieval lanes:

```bash
discovery-coscientist evidence \
  --prompt-file initial_prompt.txt \
  --max-results-per-source 50 \
  --ranking-modes relevance,recent,impact
```

Prompt files may also include retrieval settings:

```text
retrieval_settings:
  max_results_per_source: 50
  ranking_modes: relevance,recent,impact
```
