"""Offline benchmark fixtures for Discovery Co-Scientist workflows."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import DiscoveryWorkflow


@dataclass(frozen=True)
class BenchmarkFixture:
    """Small, deterministic fixture describing a benchmark scenario."""

    name: str
    workflow: DiscoveryWorkflow
    goal: str
    expected_metrics: tuple[str, ...]
    preset: str | None = None
    mock_retrieval: tuple[dict[str, str], ...] = field(default_factory=tuple)
    mock_model_outputs: dict[str, str] = field(default_factory=dict)


_COMMON_METRICS = (
    "quality",
    "cost",
    "latency",
    "duplicate_rate",
    "retrieval_hits",
    "ranking_agreement",
)


ROBIN_DRY_AMD = BenchmarkFixture(
    name="robin-dry-amd",
    workflow="therapeutic_discovery",
    goal=(
        "Robin-style dry AMD therapeutic discovery: propose disease-relevant "
        "assays, rank them, generate therapeutic candidates from the winning "
        "assay, evaluate candidates, and support experiment-informed "
        "regeneration."
    ),
    expected_metrics=_COMMON_METRICS,
    mock_retrieval=(
        {
            "source": "local_fixture",
            "title": "RPE dysfunction and complement stress in dry AMD",
            "excerpt": "Dry AMD models should connect RPE stress, complement activation, "
            "phagocytosis, mitochondrial dysfunction, and drusen biology.",
        },
        {
            "source": "local_fixture",
            "title": "Functional assays for retinal pigment epithelium rescue",
            "excerpt": "Useful assays measure phagocytosis, barrier integrity, oxidative "
            "stress resilience, inflammatory signaling, or photoreceptor support.",
        },
    ),
    mock_model_outputs={
        "assay_generation": (
            "RPE phagocytosis rescue assay with complement-stressed stem-cell "
            "derived RPE and a functional fluorescent outer-segment readout."
        ),
        "assay_evaluation": (
            "Strong disease/model relevance and feasible readout; monitor "
            "toxicity and donor-line variability."
        ),
        "candidate_generation": (
            "Generate candidates that modulate complement stress, lysosomal "
            "function, mitochondrial resilience, or cytoskeletal phagocytosis."
        ),
        "candidate_evaluation": (
            "Score target validation, disease relevance, mechanistic specificity, "
            "safety/tolerability, ADME/PK, delivery feasibility, and novelty."
        ),
    },
)


AML_GENERAL_COMPATIBILITY = BenchmarkFixture(
    name="aml-general-compatibility",
    workflow="general_hypothesis",
    goal=(
        "Produce a ranked list of drug repurposing candidates for acute "
        "myeloid leukemia (AML) under the existing general hypothesis workflow."
    ),
    expected_metrics=_COMMON_METRICS,
    preset="paper-aml",
    mock_retrieval=(
        {
            "source": "local_fixture",
            "title": "AML repurposing compatibility fixture",
            "excerpt": "General co-scientist sessions should still run generation, "
            "reflection, ranking, evolution, and meta-review for AML goals.",
        },
    ),
    mock_model_outputs={
        "generation": "Named AML repurposing hypotheses with mechanism and falsification tests.",
        "ranking": "Pairwise judge output with calibrated rationale and better idea selection.",
    },
)


BENCHMARK_FIXTURES: dict[str, BenchmarkFixture] = {
    ROBIN_DRY_AMD.name: ROBIN_DRY_AMD,
    AML_GENERAL_COMPATIBILITY.name: AML_GENERAL_COMPATIBILITY,
}


def get_benchmark_fixture(name: str) -> BenchmarkFixture:
    try:
        return BENCHMARK_FIXTURES[name]
    except KeyError as e:
        names = ", ".join(sorted(BENCHMARK_FIXTURES))
        raise KeyError(f"unknown benchmark fixture {name!r}; available: {names}") from e
