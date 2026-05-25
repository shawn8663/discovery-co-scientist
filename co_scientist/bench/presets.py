"""Built-in bench candidate presets.

Curated comparison setups so users can reproduce known benchmarks with one
flag instead of typing N `--candidate` lines.

Substitutions in the "paper" preset
-----------------------------------
The Google Co-Scientist paper compared their system against:

    Gemini 2.0 Flash Thinking Experimental 12-19
    Gemini 2.0 Pro Experimental
    OpenAI o1

across 15 expert-curated research goals. Of those, OpenRouter currently
serves only `openai/o1`; the experimental Gemini 2.0 Thinking and Pro
Experimental branches were retired. We substitute the closest current
analogues and document the swap so reported numbers are interpretable:

    paper-baseline                            current substitute
    ----------------------------------------  --------------------------------
    Gemini 2.0 Flash Thinking Experimental    google/gemini-2.0-flash-001
       (Thinking branch deprecated; production 2.0 Flash is the closest)
    Gemini 2.0 Pro Experimental               google/gemini-2.5-pro
       (2.0 Pro Experimental removed; 2.5 Pro is the current Pro-tier Gemini)
    OpenAI o1                                 openai/o1               (exact)
    Claude Haiku (added by user request)      anthropic/claude-haiku-4.5

Judge model is left configurable via --judge so users can pick a different
referee. The recommended default for this preset is
`openrouter:google/gemini-3-flash-preview`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .runner import BenchCandidate


@dataclass(frozen=True)
class BenchPreset:
    name: str
    description: str
    candidates: tuple[BenchCandidate, ...]
    suggested_judge: str        # "provider:model"


PRESETS: dict[str, BenchPreset] = {
    "paper": BenchPreset(
        name="paper",
        description=(
            "Reproduce the Co-Scientist paper's preference-ranking comparison "
            "(plus Haiku) using current OpenRouter models. See module docstring "
            "for the substitutions we had to make for retired experimental models."
        ),
        candidates=(
            BenchCandidate(
                label="gemini-2-flash-thinking",
                provider="openrouter",
                model="google/gemini-2.0-flash-001",
            ),
            BenchCandidate(
                label="gemini-2-pro",
                provider="openrouter",
                model="google/gemini-2.5-pro",
            ),
            BenchCandidate(
                label="openai-o1",
                provider="openrouter",
                model="openai/o1",
            ),
            BenchCandidate(
                label="claude-haiku-4.5",
                provider="openrouter",
                model="anthropic/claude-haiku-4.5",
            ),
        ),
        suggested_judge="openrouter:google/gemini-3-flash-preview",
    ),
}


def get_preset(name: str) -> BenchPreset:
    try:
        return PRESETS[name]
    except KeyError as e:
        names = ", ".join(sorted(PRESETS))
        raise KeyError(f"unknown bench preset {name!r}; available: {names}") from e
