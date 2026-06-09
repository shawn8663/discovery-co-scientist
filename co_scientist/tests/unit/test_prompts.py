"""Prompt rendering smoke."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from co_scientist.llm import prompts


def test_all_templates_exist_on_disk() -> None:
    for key in prompts.TEMPLATES:
        p = prompts.template_path(key)
        assert p.exists(), f"missing template file for {key}: {p}"


def test_render_parse_goal() -> None:
    out = prompts.render(
        "parse_goal",
        goal="Investigate how X causes Y in mammalian cells",
        preferences_text="testable, specific",
    )
    assert "Investigate how X causes Y" in out
    assert "testable, specific" in out


def test_render_generation_literature() -> None:
    out = prompts.render(
        "generation.literature",
        goal="goal",
        preferences="prefs",
        articles_with_reasoning="(articles)",
    )
    assert "Goal: goal" in out
    assert "(articles)" in out
    assert "record_hypothesis" in out


def test_render_ranking_pairwise() -> None:
    out = prompts.render(
        "ranking.pairwise",
        goal="g",
        idea_attributes="novel, testable",
        hypothesis_1="H1 prose",
        hypothesis_1_id="H1",
        hypothesis_2="H2 prose",
        hypothesis_2_id="H2",
        review_1="R1",
        review_2="R2",
    )
    assert "better idea: <1 or 2>" in out
    assert "H1 prose" in out


def test_render_ranking_debate_stays_within_verdict_budget() -> None:
    out = prompts.render(
        "ranking.debate",
        goal="g",
        preferences="p",
        hypothesis_1="H1 prose",
        hypothesis_1_id="H1",
        hypothesis_2="H2 prose",
        hypothesis_2_id="H2",
        review_1="R1",
        review_2="R2",
    )

    assert "Use no more than two compact turns" in out
    assert "Do not write a long simulated transcript" in out
    assert "3 to 5" not in out


def test_render_unknown_template_raises() -> None:
    with pytest.raises(KeyError):
        prompts.render("nonexistent.template")


@pytest.mark.parametrize(
    ("template_key", "variables", "required"),
    [
        (
            "generation.literature",
            {"goal": "g", "preferences": "p", "articles_with_reasoning": "lit"},
            ("Goal:", "Criteria", "record_hypothesis", "citations"),
        ),
        (
            "generation.debate",
            {"goal": "g", "preferences": "p", "transcript": "prior"},
            ("Goal:", "Criteria", "HYPOTHESIS", "record_hypothesis"),
        ),
        (
            "reflection.screen",
            {"goal": "g", "preferences": "p", "hypothesis_id": "h", "hypothesis_text": "text"},
            ("Goal:", "Hypothesis", "record_review", 'kind="screen"'),
        ),
        (
            "reflection.full",
            {
                "goal": "g",
                "preferences": "p",
                "hypothesis_id": "h",
                "hypothesis_text": "text",
                "articles_block": "articles",
            },
            ("Goal:", "Hypothesis", "Retrieved literature", "record_review", "evidence"),
        ),
        (
            "reflection.verification",
            {"goal": "g", "hypothesis_id": "h", "hypothesis_text": "text"},
            ("Goal:", "Procedure", "record_review", 'kind="verification"'),
        ),
        (
            "reflection.observation",
            {
                "article_id": "a",
                "article_hash": "hash",
                "article": "article",
                "hypothesis_id": "h",
                "hypothesis": "hyp",
            },
            ("UNTRUSTED_SOURCE", "HYPOTHESIS_TEXT", "record_review", 'kind="observation"'),
        ),
        (
            "ranking.pairwise",
            {
                "goal": "g",
                "preferences": "p",
                "hypothesis_1_id": "h1",
                "hypothesis_1": "h1 text",
                "hypothesis_2_id": "h2",
                "hypothesis_2": "h2 text",
                "review_1": "r1",
                "review_2": "r2",
            },
            ("Hypothesis 1", "Review of hypothesis 1", "better idea: <1 or 2>"),
        ),
        (
            "ranking.debate",
            {
                "goal": "g",
                "preferences": "p",
                "hypothesis_1_id": "h1",
                "hypothesis_1": "h1 text",
                "hypothesis_2_id": "h2",
                "hypothesis_2": "h2 text",
                "review_1": "r1",
                "review_2": "r2",
            },
            ("Debate procedure", "Initial review", "better idea: "),
        ),
        (
            "evolution.combine",
            {
                "goal": "g",
                "preferences": "p",
                "hypothesis_a_id": "ha",
                "hypothesis_a": "ha text",
                "hypothesis_b_id": "hb",
                "hypothesis_b": "hb text",
            },
            ("Goal:", "Hypothesis A", "Hypothesis B", "record_hypothesis", 'strategy="combine"'),
        ),
        (
            "evolution.simplify",
            {"goal": "g", "preferences": "p", "hypothesis_id": "h", "hypothesis": "text"},
            ("Goal:", "Original hypothesis", "record_hypothesis", 'strategy="simplify"'),
        ),
        (
            "evolution.feasibility",
            {"goal": "g", "preferences": "p", "hypothesis_id": "h", "hypothesis": "text"},
            ("Goal:", "Evaluation Criteria", "record_hypothesis", "parent_ids"),
        ),
        (
            "evolution.out_of_box",
            {
                "goal": "g",
                "preferences": "p",
                "hypotheses": [SimpleNamespace(id="h", text="text")],
            },
            ("Goal:", "CORE HYPOTHESIS", "record_hypothesis", "parent_ids"),
        ),
        (
            "metareview.system",
            {"goal": "g", "preferences": "p", "reviews": "reviews"},
            ("Goal:", "Provided reviews", "record_system_feedback", "suggested_focus_areas"),
        ),
        (
            "metareview.final",
            {"goal": "g", "preferences": "p", "top_hypotheses_block": "top"},
            ("# Executive summary", "# Main research directions", "# Caveats and limitations"),
        ),
    ],
)
def test_supplement_9_prompt_contracts(template_key, variables, required) -> None:
    out = prompts.render(template_key, **variables)

    for marker in required:
        assert marker in out
