"""Tests for the pre-flight cost estimator."""

from __future__ import annotations

from co_scientist.config import Config
from co_scientist.llm.estimator import EstimateContext, estimate, format_summary


def test_estimate_emits_warning_when_budget_too_low() -> None:
    cfg = Config()
    cfg.run.budget_usd = 1.0
    est = estimate(cfg)
    assert est.total_usd > cfg.run.budget_usd
    assert est.warning is not None and "exceeds" in est.warning


def test_estimate_no_warning_when_budget_generous() -> None:
    cfg = Config()
    cfg.run.budget_usd = 9999.0
    est = estimate(cfg)
    assert est.warning is None


def test_estimate_rows_include_all_phases() -> None:
    cfg = Config()
    est = estimate(cfg)
    labels = {r.label for r in est.rows}
    assert {
        "parse_goal", "generation.initial", "reflection.full",
        "ranking_pairwise", "metareview.final",
    } <= labels


def test_estimate_scales_with_max_ideas() -> None:
    cfg = Config()
    small = estimate(cfg, max_ideas=10, max_matches_per_idea=4)
    big = estimate(cfg, max_ideas=100, max_matches_per_idea=12)
    assert big.total_usd > small.total_usd * 5


def test_general_estimate_uses_run_context_and_project_files(tmp_path) -> None:
    cfg = Config()
    background = tmp_path / "background.txt"
    background.write_text("x" * 40_000)

    small = estimate(
        cfg,
        context=EstimateContext(
            workflow="general_hypothesis",
            goal="short goal",
            n_initial=1,
            project_files=[],
        ),
    )
    rich = estimate(
        cfg,
        context=EstimateContext(
            workflow="general_hypothesis",
            goal="short goal",
            preferences_text="prefer mechanistic novelty",
            n_initial=6,
            project_files=[background],
        ),
    )

    assert rich.expected_usd > small.expected_usd
    assert rich.context["project_file_count"] == 1
    assert rich.context["project_context_tokens"] > 0
    generation = next(r for r in rich.rows if r.label == "generation.initial")
    assert generation.n_calls == 6


def test_therapeutic_estimate_uses_robin_rows() -> None:
    cfg = Config()

    est = estimate(
        cfg,
        context=EstimateContext(
            workflow="therapeutic_discovery",
            goal="Discover therapeutics for dry AMD",
            n_initial=3,
        ),
    )

    labels = {r.label for r in est.rows}
    assert "assay.generate" in labels
    assert "candidate.evaluate" in labels
    assert "reflection.full" not in labels
    assert est.context["workflow"] == "therapeutic_discovery"


def test_format_summary_reports_expected_and_conservative() -> None:
    cfg = Config()
    est = estimate(
        cfg,
        context=EstimateContext(
            workflow="therapeutic_discovery",
            goal="Discover therapeutics for dry AMD",
            project_files=[],
        ),
    )

    text = format_summary(est, budget_usd=cfg.run.budget_usd)

    assert "expected:" in text
    assert "conservative:" in text
    assert "workflow: therapeutic_discovery" in text
    assert "main drivers:" in text
