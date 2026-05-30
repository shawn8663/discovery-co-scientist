You are doing a cheap first-pass screen of a scientific hypothesis before any retrieval-heavy review.

Goal: {{ goal }}

Preferences / criteria:
{{ preferences | default('') }}

Hypothesis under screen:
<HYPOTHESIS_TEXT id="{{ hypothesis_id }}">
{{ hypothesis_text }}
</HYPOTHESIS_TEXT_END id="{{ hypothesis_id }}">

Use only the research goal, preferences, and hypothesis text above. Do not ask for web search, literature retrieval, PDF lookup, code execution, or any other external tool.

Your task:
1. Decide whether this hypothesis is promising enough to deserve a full literature-backed review.
2. Score novelty, correctness, testability, and feasibility from 0.0 to 1.0 using only internal plausibility and the stated goal.
3. Choose exactly one verdict: `missing_piece`, `neutral`, `already_explained`, `other_more_likely`, or `disproved`.

Treat `missing_piece` and `neutral` as promising enough for full review unless your notes clearly identify a fatal flaw. Treat `already_explained`, `other_more_likely`, and `disproved` as low promise.

When finished, call the `record_review` tool with `kind="screen"`. Leave `evidence` empty because no external sources were retrieved. Put the screening rationale in `notes`.
