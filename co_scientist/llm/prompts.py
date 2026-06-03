"""Jinja2-based prompt loader.

Templates live in `config/prompts/*.md`. Each agent.mode maps to one template.
The loader pre-renders the variables the caller provides; missing variables fall
through Jinja's `default(...)` filters used inside the templates.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape

from ..config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"


# Mapping is the source of truth for "what does each agent.mode call?"
TEMPLATES = {
    "parse_goal": "parse_goal.md",
    "generation.literature": "generation_literature.md",
    "generation.debate": "generation_debate.md",
    "reflection.screen": "reflection_screen.md",
    "reflection.full": "reflection_review.md",
    "reflection.verification": "reflection_verification.md",
    "reflection.observation": "reflection_observation.md",
    "ranking.pairwise": "ranking_pairwise.md",
    "ranking.debate": "ranking_debate.md",
    "evolution.feasibility": "evolution_feasibility.md",
    "evolution.combine": "evolution_combine.md",
    "evolution.simplify": "evolution_simplify.md",
    "evolution.out_of_box": "evolution_out_of_box.md",
    "metareview.system": "metareview_system.md",
    "metareview.final": "metareview_final.md",
    "robin.assay_query_generation": "robin_assay_query_generation.md",
    "robin.assay_generation": "robin_assay_generation.md",
    "robin.assay_evaluation": "robin_assay_evaluation.md",
    "robin.assay_pairwise_judge": "robin_assay_pairwise_judge.md",
    "robin.candidate_goal_synthesis": "robin_candidate_goal_synthesis.md",
    "robin.candidate_query_generation": "robin_candidate_query_generation.md",
    "robin.candidate_generation": "robin_candidate_generation.md",
    "robin.candidate_evaluation": "robin_candidate_evaluation.md",
    "robin.candidate_pairwise_judge": "robin_candidate_pairwise_judge.md",
    "robin.flow_cytometry_analysis": "robin_flow_cytometry_analysis.md",
    "robin.rnaseq_analysis": "robin_rnaseq_analysis.md",
    "robin.result_interpretation": "robin_result_interpretation.md",
}


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        autoescape=select_autoescape(disabled_extensions=("md",), default=False),
        undefined=ChainableUndefined,    # missing vars → falsy in {% if %}, "" elsewhere
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def render(template_key: str, **variables: Any) -> str:
    """Render a template by its agent.mode key, e.g. 'reflection.verification'.

    Variables not used by the template are silently ignored. Variables referenced
    by the template but not supplied raise an error — use the `default(...)`
    filter in the template for genuinely optional fields.
    """
    if template_key not in TEMPLATES:
        raise KeyError(f"unknown prompt template: {template_key!r}")
    template = _env().get_template(TEMPLATES[template_key])
    return template.render(**variables)


def list_templates() -> list[str]:
    """For `co-scientist tools list`-style introspection."""
    return sorted(TEMPLATES.keys())


def template_path(key: str) -> Path:
    return PROMPTS_DIR / TEMPLATES[key]
