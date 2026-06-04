"""Pre-flight cost estimator.

Given a parsed ResearchPlan and config, predict the session's expected USD
spend. Used by `co-scientist run` to surface a warning before launching when
the estimate is more than ~1.2x the budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from .routing import estimate_cost_usd


@dataclass(frozen=True)
class CallEstimate:
    label: str
    n_calls: int
    model: str
    input_tokens_each: int
    output_tokens_each: int
    cache_ratio: float       # 0..1, fraction of inputs that come from cache

    @property
    def cached_in(self) -> int:
        return int(self.input_tokens_each * self.cache_ratio)

    @property
    def uncached_in(self) -> int:
        return self.input_tokens_each - self.cached_in

    def usd(self) -> float:
        per = estimate_cost_usd(
            model=self.model,
            input_tokens=self.uncached_in,
            output_tokens=self.output_tokens_each,
            cache_read=self.cached_in,
            cache_write=0,
        )
        return per * self.n_calls


@dataclass(frozen=True)
class SessionEstimate:
    rows: list[CallEstimate]
    total_usd: float
    expected_usd: float
    conservative_usd: float
    conservative_rows: list[CallEstimate]
    context: dict[str, Any]
    drivers: list[str]
    warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "workflow": self.context.get("workflow"),
            "context": self.context,
            "drivers": self.drivers,
            "rows": [
                {
                    "label": r.label, "n_calls": r.n_calls, "model": r.model,
                    "input_tokens_each": r.input_tokens_each,
                    "output_tokens_each": r.output_tokens_each,
                    "cache_ratio": r.cache_ratio, "usd": r.usd(),
                }
                for r in self.rows
            ],
            "conservative_rows": [
                {
                    "label": r.label, "n_calls": r.n_calls, "model": r.model,
                    "input_tokens_each": r.input_tokens_each,
                    "output_tokens_each": r.output_tokens_each,
                    "cache_ratio": r.cache_ratio, "usd": r.usd(),
                }
                for r in self.conservative_rows
            ],
            "expected_usd": self.expected_usd,
            "conservative_usd": self.conservative_usd,
            "total_usd": self.total_usd,
            "warning": self.warning,
        }


@dataclass(frozen=True)
class EstimateContext:
    workflow: str = "general_hypothesis"
    goal: str = ""
    preferences_text: str | None = None
    project_files: list[Path] | None = None
    n_initial: int | None = None
    wall_clock_seconds: int | None = None
    num_assays: int = 10
    num_candidates: int = 30
    regeneration_candidates: int = 10
    analysis_trajectories: int = 0


def estimate(cfg: Config, *, max_ideas: int | None = None,
             max_matches_per_idea: int | None = None,
             context: EstimateContext | None = None) -> SessionEstimate:
    """Workflow-aware pre-flight estimate.

    The no-context call remains supported for `discovery-coscientist estimate`,
    but callers that know the launch arguments should pass `EstimateContext`.
    """
    context = context or EstimateContext()
    n_ideas = max_ideas or cfg.run.max_ideas
    matches_per = max_matches_per_idea or cfg.run.max_matches_per_idea
    ctx = _context_dict(cfg, context)
    if context.workflow == "therapeutic_discovery":
        rows, conservative_rows = _therapeutic_rows(cfg, context, ctx)
    else:
        rows, conservative_rows = _general_rows(cfg, context, ctx, n_ideas, matches_per)
    expected = sum(r.usd() for r in rows)
    conservative = sum(r.usd() for r in conservative_rows)
    total = conservative
    warn = None
    if conservative > cfg.run.budget_usd * 1.2:
        warn = (
            f"Conservative estimated cost ${conservative:.2f} exceeds 120% of budget "
            f"${cfg.run.budget_usd:.2f}. Consider reducing max_ideas, lowering "
            f"max_matches_per_idea, reducing workflow counts, or raising --budget-usd."
        )
    return SessionEstimate(
        rows=rows,
        total_usd=total,
        expected_usd=expected,
        conservative_usd=conservative,
        conservative_rows=conservative_rows,
        context=ctx,
        drivers=_drivers(ctx),
        warning=warn,
    )


def format_summary(est: SessionEstimate, *, budget_usd: float) -> str:
    """Human-readable terminal summary for pre-flight estimates."""
    drivers = ", ".join(est.drivers[:4]) if est.drivers else "configured models"
    return "\n".join(
        [
            "Pre-flight estimate:",
            f"  expected: ${est.expected_usd:.2f}",
            f"  conservative: ${est.conservative_usd:.2f}",
            f"  budget: ${budget_usd:.2f}",
            f"  workflow: {est.context.get('workflow')}",
            f"  main drivers: {drivers}",
        ]
    )


def _general_rows(
    cfg: Config,
    context: EstimateContext,
    ctx: dict[str, Any],
    max_ideas: int,
    matches_per: int,
) -> tuple[list[CallEstimate], list[CallEstimate]]:
    n_initial = max(1, int(context.n_initial or 3))
    wall_factor = _wall_clock_factor(ctx["wall_clock_seconds"], cfg.run.wall_clock_seconds)
    expected_ideas = min(max_ideas, max(n_initial, int(max_ideas * 0.35 * wall_factor), n_initial * 4))
    conservative_ideas = max(max_ideas, n_initial)
    return (
        _general_rows_for_count(cfg, ctx, expected_ideas, n_initial, matches_per),
        _general_rows_for_count(cfg, ctx, conservative_ideas, n_initial, matches_per),
    )


def _general_rows_for_count(
    cfg: Config,
    ctx: dict[str, Any],
    n_ideas: int,
    n_initial: int,
    matches_per: int,
) -> list[CallEstimate]:
    n_initial = min(n_initial, n_ideas)
    n_followup = max(0, n_ideas - n_initial)
    n_matches = n_ideas * matches_per // 2
    n_evolutions = int(n_ideas * 0.4)
    n_meta_periodic = max(1, n_matches // 50)
    context_tokens = ctx["goal_tokens"] + ctx["preferences_tokens"] + ctx["project_context_tokens"]
    literature_tokens = min(10_000, max(0, context_tokens // 2))
    return [
        CallEstimate(
            label="parse_goal", n_calls=1, model=cfg.models.parse_goal,
            input_tokens_each=600 + ctx["goal_tokens"] + ctx["preferences_tokens"],
            output_tokens_each=300, cache_ratio=0.0,
        ),
        CallEstimate(
            label="generation.initial", n_calls=n_initial, model=cfg.models.generation,
            input_tokens_each=8_000 + context_tokens,
            output_tokens_each=2_500 + cfg.thinking.generation_literature,
            cache_ratio=0.45,
        ),
        CallEstimate(
            label="generation.followup", n_calls=n_followup, model=cfg.models.generation,
            input_tokens_each=12_000 + literature_tokens,
            output_tokens_each=2_500 + cfg.thinking.generation_literature,
            cache_ratio=0.55,
        ),
        CallEstimate(
            label="reflection.full", n_calls=n_ideas, model=cfg.models.reflection,
            input_tokens_each=10_000 + literature_tokens,
            output_tokens_each=2_000 + cfg.thinking.reflection_full,
            cache_ratio=0.55,
        ),
        CallEstimate(
            label="reflection.verification", n_calls=n_ideas,
            model=cfg.models.reflection,
            input_tokens_each=9_000 + literature_tokens,
            output_tokens_each=3_000 + cfg.thinking.reflection_verification,
            cache_ratio=0.55,
        ),
        CallEstimate(
            label="ranking_pairwise", n_calls=n_matches,
            model=cfg.models.ranking_pairwise,
            input_tokens_each=8_000,
            output_tokens_each=800 + cfg.thinking.ranking_pairwise,
            cache_ratio=0.70,
        ),
        CallEstimate(
            label="evolution", n_calls=n_evolutions, model=cfg.models.evolution,
            input_tokens_each=12_000 + min(6_000, literature_tokens),
            output_tokens_each=2_500 + cfg.thinking.evolution_combine,
            cache_ratio=0.50,
        ),
        CallEstimate(
            label="metareview.system", n_calls=n_meta_periodic,
            model=cfg.models.metareview_feedback,
            input_tokens_each=14_000,
            output_tokens_each=2_000 + cfg.thinking.metareview_feedback,
            cache_ratio=0.50,
        ),
        CallEstimate(
            label="metareview.final", n_calls=1,
            model=cfg.models.metareview_final,
            input_tokens_each=24_000,
            output_tokens_each=6_000 + cfg.thinking.metareview_final,
            cache_ratio=0.40,
        ),
    ]


def _therapeutic_rows(
    cfg: Config,
    context: EstimateContext,
    ctx: dict[str, Any],
) -> tuple[list[CallEstimate], list[CallEstimate]]:
    num_assays = max(1, context.num_assays)
    num_candidates = max(1, context.num_candidates)
    conservative_candidates = max(num_candidates, int(num_candidates * 1.5))
    expected = _therapeutic_rows_for_counts(
        cfg, ctx, num_assays=num_assays, num_candidates=num_candidates,
        regeneration_candidates=0, analysis_trajectories=context.analysis_trajectories,
    )
    conservative = _therapeutic_rows_for_counts(
        cfg, ctx, num_assays=num_assays, num_candidates=conservative_candidates,
        regeneration_candidates=context.regeneration_candidates,
        analysis_trajectories=max(context.analysis_trajectories, 3),
    )
    return expected, conservative


def _therapeutic_rows_for_counts(
    cfg: Config,
    ctx: dict[str, Any],
    *,
    num_assays: int,
    num_candidates: int,
    regeneration_candidates: int,
    analysis_trajectories: int,
) -> list[CallEstimate]:
    context_tokens = ctx["goal_tokens"] + ctx["preferences_tokens"] + ctx["project_context_tokens"]
    assay_rank_matches = max(0, num_assays * (num_assays - 1) // 2)
    candidate_rank_matches = max(0, num_candidates * min(8, max(1, num_candidates - 1)) // 2)
    rows = [
        CallEstimate(
            label="parse_goal", n_calls=1, model=cfg.models.parse_goal,
            input_tokens_each=600 + ctx["goal_tokens"] + ctx["preferences_tokens"],
            output_tokens_each=300, cache_ratio=0.0,
        ),
        CallEstimate(
            label="assay.generate", n_calls=1, model=cfg.models.generation,
            input_tokens_each=7_000 + context_tokens,
            output_tokens_each=4_096 + cfg.thinking.generation_literature,
            cache_ratio=0.45,
        ),
        CallEstimate(
            label="assay.evaluate", n_calls=num_assays, model=cfg.models.generation,
            input_tokens_each=6_000 + min(8_000, context_tokens // 2),
            output_tokens_each=4_096 + cfg.thinking.generation_literature,
            cache_ratio=0.45,
        ),
        CallEstimate(
            label="assay.rank_btl", n_calls=assay_rank_matches,
            model=cfg.models.ranking_pairwise,
            input_tokens_each=5_000, output_tokens_each=800 + cfg.thinking.ranking_pairwise,
            cache_ratio=0.60,
        ),
        CallEstimate(
            label="candidate.generate", n_calls=1, model=cfg.models.generation,
            input_tokens_each=8_000 + min(8_000, context_tokens // 2),
            output_tokens_each=4_096 + cfg.thinking.generation_literature,
            cache_ratio=0.45,
        ),
        CallEstimate(
            label="candidate.evaluate", n_calls=num_candidates, model=cfg.models.generation,
            input_tokens_each=6_500 + min(8_000, context_tokens // 3),
            output_tokens_each=4_096 + cfg.thinking.generation_literature,
            cache_ratio=0.45,
        ),
        CallEstimate(
            label="candidate.rank_btl", n_calls=candidate_rank_matches,
            model=cfg.models.ranking_pairwise,
            input_tokens_each=5_500, output_tokens_each=800 + cfg.thinking.ranking_pairwise,
            cache_ratio=0.60,
        ),
        CallEstimate(
            label="metareview.final", n_calls=1, model=cfg.models.metareview_final,
            input_tokens_each=18_000 + min(8_000, context_tokens // 4),
            output_tokens_each=6_000 + cfg.thinking.metareview_final,
            cache_ratio=0.40,
        ),
    ]
    if analysis_trajectories:
        rows.append(
            CallEstimate(
                label="analysis.trajectory", n_calls=analysis_trajectories,
                model=cfg.models.generation,
                input_tokens_each=8_000 + min(8_000, context_tokens // 2),
                output_tokens_each=4_096 + cfg.thinking.generation_literature,
                cache_ratio=0.30,
            )
        )
        rows.append(
            CallEstimate(
                label="result_interpretation", n_calls=1, model=cfg.models.generation,
                input_tokens_each=10_000,
                output_tokens_each=3_000 + cfg.thinking.generation_literature,
                cache_ratio=0.40,
            )
        )
    if regeneration_candidates:
        rows.append(
            CallEstimate(
                label="candidate.regenerate_from_results", n_calls=1, model=cfg.models.generation,
                input_tokens_each=10_000 + min(8_000, context_tokens // 2),
                output_tokens_each=4_096 + cfg.thinking.generation_literature,
                cache_ratio=0.45,
            )
        )
        rows.append(
            CallEstimate(
                label="candidate.evaluate_regenerated", n_calls=regeneration_candidates,
                model=cfg.models.generation,
                input_tokens_each=6_500 + min(8_000, context_tokens // 3),
                output_tokens_each=4_096 + cfg.thinking.generation_literature,
                cache_ratio=0.45,
            )
        )
    return rows


def _context_dict(cfg: Config, context: EstimateContext) -> dict[str, Any]:
    project_files = context.project_files or []
    project_tokens = sum(_file_context_tokens(path) for path in project_files)
    return {
        "workflow": context.workflow,
        "goal_tokens": _text_tokens(context.goal),
        "preferences_tokens": _text_tokens(context.preferences_text or ""),
        "project_file_count": len(project_files),
        "project_context_tokens": project_tokens,
        "n_initial": context.n_initial or 3,
        "wall_clock_seconds": context.wall_clock_seconds or cfg.run.wall_clock_seconds,
        "num_assays": context.num_assays,
        "num_candidates": context.num_candidates,
        "analysis_trajectories": context.analysis_trajectories,
    }


def _drivers(ctx: dict[str, Any]) -> list[str]:
    drivers = [str(ctx["workflow"])]
    if ctx["project_file_count"]:
        drivers.append(
            f"{ctx['project_file_count']} project file(s), ~{ctx['project_context_tokens']} context tokens"
        )
    if ctx["preferences_tokens"]:
        drivers.append(f"preferences ~{ctx['preferences_tokens']} tokens")
    if ctx["workflow"] == "therapeutic_discovery":
        drivers.append(f"{ctx['num_assays']} assays")
        drivers.append(f"{ctx['num_candidates']} candidates")
    else:
        drivers.append(f"n_initial={ctx['n_initial']}")
    return drivers


def _file_context_tokens(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    # Approximate text/PDF extraction payload while capping any one file so
    # large PDFs do not dominate a pre-flight estimate unrealistically.
    return min(30_000, max(100, size // 4))


def _text_tokens(text: str) -> int:
    return max(0, len(text) // 4)


def _wall_clock_factor(seconds: int, default_seconds: int) -> float:
    if default_seconds <= 0:
        return 1.0
    return max(0.15, min(1.0, seconds / default_seconds))
