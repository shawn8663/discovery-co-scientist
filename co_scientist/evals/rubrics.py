"""LLM-as-judge rubric scoring.

The judge is a separate Anthropic call (defaults to Sonnet) that takes:
- a candidate artifact (hypothesis record / review record / final overview)
- a rubric: list of criteria with name, weight, scoring guidance

and returns per-criterion 1-5 scores + a total. We do NOT use the same model
as the agent under test, to reduce echo-judge bias.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic

from ..config import Config


@dataclass(frozen=True)
class RubricCriterion:
    name: str
    weight: float = 1.0
    guidance: str = ""


JUDGE_TOOL: dict[str, Any] = {
    "name": "record_rubric_score",
    "description": "Record per-criterion 1-5 scores and a brief rationale.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":      {"type": "string"},
                        "score":     {"type": "integer", "minimum": 1, "maximum": 5},
                        "rationale": {"type": "string"},
                    },
                    "required": ["name", "score", "rationale"],
                },
            },
            "overall_notes": {"type": "string"},
        },
        "required": ["scores"],
    },
}


def weighted_total(rubric: list[RubricCriterion], scores: list[dict[str, Any]]) -> float:
    by_name = {s["name"]: int(s["score"]) for s in scores}
    num = 0.0
    den = 0.0
    for c in rubric:
        if c.name in by_name:
            num += c.weight * by_name[c.name]
            den += c.weight * 5.0
    return (num / den) if den > 0 else 0.0


async def judge(
    cfg: Config,
    *,
    rubric: list[RubricCriterion],
    candidate: str,
    label: str,
) -> dict[str, Any]:
    """Issue one judge call. Returns {scores: [...], weighted: float}.

    No retries here — the eval runner aggregates over many fixtures, so a single
    flaky judgment is noise we accept.
    """
    api_key = cfg.secrets.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return {"scores": [], "weighted": 0.0, "notes": "no ANTHROPIC_API_KEY"}

    rubric_text = "\n".join(
        f"- {c.name} (weight {c.weight}): {c.guidance}" for c in rubric
    )
    system = (
        "You are a calibrated evaluator. Score the candidate against each "
        "criterion on a 1-5 integer scale with 1 = poor and 5 = excellent. "
        "Be parsimonious; reserve 5 for exemplary work. Always call "
        "record_rubric_score."
    )
    user = (
        f"Candidate to evaluate (label={label}):\n\n"
        f"<CANDIDATE>\n{candidate[:12_000]}\n</CANDIDATE>\n\n"
        f"Rubric:\n{rubric_text}"
    )
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model=cfg.models.judge,
        system=system,
        max_tokens=1024,
        tools=[JUDGE_TOOL],
        tool_choice={"type": "tool", "name": "record_rubric_score"},
        messages=[{"role": "user", "content": user}],
    )
    for b in resp.content:
        if getattr(b, "type", None) == "tool_use" and getattr(b, "name", "") == "record_rubric_score":
            inp = getattr(b, "input", None)
            if isinstance(inp, dict):
                scores = inp.get("scores", [])
                return {
                    "scores": scores,
                    "weighted": weighted_total(rubric, scores),
                    "notes": inp.get("overall_notes", ""),
                }
    return {"scores": [], "weighted": 0.0, "notes": "no tool_use in response"}


# Pre-built rubrics for the four agents that have measurable outputs.

GENERATION_RUBRIC = [
    RubricCriterion("novelty", 1.0,
                    "Differs meaningfully from established literature."),
    RubricCriterion("specificity", 1.0,
                    "Names concrete entities, mechanisms, expected outcomes."),
    RubricCriterion("citation_grounding", 1.0,
                    "Citations support the claims; URLs look real and relevant."),
    RubricCriterion("testability", 1.0,
                    "Proposes a measurable, near-term experiment."),
]

REFLECTION_RUBRIC = [
    RubricCriterion("assumption_decomposition", 1.0,
                    "Breaks the hypothesis into testable assumptions."),
    RubricCriterion("evidence_quality", 1.0,
                    "Cites URLs with verbatim excerpts for each factual claim."),
    RubricCriterion("verdict_consistency", 1.0,
                    "The verdict matches the body of the review."),
]

RANKING_RUBRIC = [
    RubricCriterion("verdict_clarity", 1.0,
                    "Ends with 'better idea: 1' or 'better idea: 2'."),
    RubricCriterion("reasoning_quality", 1.0,
                    "Rationale references concrete differences, not vibes."),
    RubricCriterion("order_independence", 0.5,
                    "Verdict would not depend on which hypothesis was listed first."),
]

OVERVIEW_RUBRIC = [
    RubricCriterion("novelty", 1.0,
                    "Lead directions differ from boilerplate research summaries."),
    RubricCriterion("plausibility", 1.0,
                    "Mechanisms are physically / biologically reasonable."),
    RubricCriterion("testability", 1.0,
                    "Proposes concrete experiments for each direction."),
    RubricCriterion("specificity", 1.0,
                    "Entities, doses, timeframes are named."),
    RubricCriterion("diversity", 0.5,
                    "Top directions are meaningfully distinct."),
    RubricCriterion("citation_honesty", 1.0,
                    "URLs cited actually exist and are relevant to the claim."),
]
