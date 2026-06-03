"""Prompt contracts for Robin-style therapeutic discovery workflow."""

from __future__ import annotations

import pytest

from co_scientist.llm import prompts

ROBIN_TEMPLATE_KEYS = [
    "robin.assay_query_generation",
    "robin.assay_generation",
    "robin.assay_evaluation",
    "robin.assay_pairwise_judge",
    "robin.candidate_goal_synthesis",
    "robin.candidate_query_generation",
    "robin.candidate_generation",
    "robin.candidate_evaluation",
    "robin.candidate_pairwise_judge",
    "robin.flow_cytometry_analysis",
    "robin.rnaseq_analysis",
    "robin.result_interpretation",
]


def test_robin_templates_are_registered_and_exist() -> None:
    for key in ROBIN_TEMPLATE_KEYS:
        assert key in prompts.TEMPLATES
        assert prompts.template_path(key).exists()


@pytest.mark.parametrize(
    ("template_key", "variables", "required"),
    [
        (
            "robin.assay_generation",
            {"disease_name": "dry AMD", "num_assays": 3, "assay_lit_review_output": "lit"},
            ("strategy_name", "reasoning", "cell culture assays", "functional endpoints"),
        ),
        (
            "robin.assay_pairwise_judge",
            {
                "disease_name": "dry AMD",
                "assay_a": "A",
                "assay_b": "B",
            },
            ("Winner", "Loser", "simplicity", "biological relevance", "JSON"),
        ),
        (
            "robin.candidate_generation",
            {
                "disease_name": "dry AMD",
                "num_candidates": 3,
                "therapeutic_candidate_review_output": "lit",
            },
            (
                "CANDIDATE:",
                "HYPOTHESIS:",
                "REASONING:",
                "Strong Target Validation",
                "Developmental Feasibility",
            ),
        ),
        (
            "robin.candidate_pairwise_judge",
            {
                "disease_name": "dry AMD",
                "candidate_a": "A",
                "candidate_b": "B",
            },
            (
                "Target Validation",
                "Safety",
                "ADME",
                "Drug Delivery",
                "Winner",
                "Loser",
            ),
        ),
        (
            "robin.result_interpretation",
            {
                "disease_name": "dry AMD",
                "analysis_summary": "Ripasudil increased MFI",
            },
            (
                "positive_hits",
                "negative_hits",
                "suggested_mechanisms",
                "follow_up_assays",
                "constraints",
            ),
        ),
    ],
)
def test_robin_prompt_contracts(template_key, variables, required) -> None:
    out = prompts.render(template_key, **variables)
    for marker in required:
        assert marker in out
