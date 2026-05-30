"""Tests for agent helper functions that don't require an LLM call."""

from __future__ import annotations

from co_scientist.agents.generation import _filter_to_seen_urls, _render_hypothesis_md
from co_scientist.agents.ranking import _digest_review_for_ranking
from co_scientist.agents.reflection import _render_review_md


def test_citation_url_filter_keeps_only_seen() -> None:
    citations = [
        {"title": "A", "url": "https://a.example/paper1"},
        {"title": "B", "url": "https://hallucinated.example/paper2"},
        {"title": "C", "url": "https://c.example/paper3"},
        {"no_url": True},
    ]
    seen = {"https://a.example/paper1", "https://c.example/paper3"}
    out = _filter_to_seen_urls(citations, seen)
    urls = {c["url"] for c in out}
    assert urls == seen
    # hallucinated URL is dropped
    assert "https://hallucinated.example/paper2" not in urls


def test_hypothesis_md_renders_sections() -> None:
    md = _render_hypothesis_md(
        {
            "title": "T",
            "statement": "S",
            "mechanism": "M",
            "entities": ["E1", "E2"],
            "anticipated_outcomes": "AO",
            "novelty_argument": "N",
            "citations": [
                {"title": "Paper", "url": "https://example.com/x", "year": 2024}
            ],
        }
    )
    for marker in ("# T", "**Hypothesis.** S", "## Mechanism", "## Entities",
                   "## Anticipated outcomes", "## Novelty", "## Citations",
                   "https://example.com/x"):
        assert marker in md


def test_review_md_renders_sections() -> None:
    md = _render_review_md(
        {
            "verdict": "missing_piece",
            "novelty": 0.7, "correctness": 0.5, "testability": 0.6,
            "assumptions": [
                {"assumption": "A1", "plausibility": "plausible", "rationale": "R1"}
            ],
            "evidence": [
                {"claim": "claim1", "url": "https://e.example/p", "excerpt": "quote"}
            ],
            "notes": "n",
        }
    )
    assert "Verdict" in md
    assert "novelty 0.70" in md
    assert "plausible" in md
    assert "https://e.example/p" in md
    assert "n" in md


def test_ranking_review_digest_preserves_decision_signals() -> None:
    long_notes = " ".join(f"tail-{i}" for i in range(400))
    body = (
        "# Review\n\n"
        "**Verdict.** missing_piece\n\n"
        "**Scores.** novelty 0.90 · correctness 0.60 · testability 0.80\n\n"
        "## Assumptions\n"
        "- *plausible*: A1\n  R1\n"
        "- *uncertain*: A2\n  R2\n\n"
        "## Evidence\n"
        "- Claim one — https://example.test/one\n  > quote one\n"
        "- Claim two — https://example.test/two\n  > quote two\n\n"
        f"## Notes\n{long_notes}"
    )

    digest = _digest_review_for_ranking(body, max_chars=450)

    assert len(digest) <= 450
    assert "**Verdict.** missing_piece" in digest
    assert "novelty 0.90" in digest
    assert "## Evidence" in digest
    assert "tail-399" not in digest


def test_ranking_review_digest_leaves_short_reviews_intact() -> None:
    body = "# Review\n\n**Verdict.** neutral\n\nBrief."

    assert _digest_review_for_ranking(body, max_chars=450) == body
