"""Reusable safety gate helpers for goals, hypotheses, and reports."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from .classifier import Action, ClassifierResult, SafetyClassifier


@dataclass(frozen=True)
class SafetyGateAssessment:
    result: ClassifierResult
    action: Action

    @property
    def should_stop(self) -> bool:
        return self.action in {"block", "quarantine"}

    def artifact(self) -> dict[str, object]:
        return {
            "action": self.action,
            "categories": self.result.categories,
            "confidence": self.result.confidence,
            "rationale": self.result.rationale,
        }


async def assess_safety(cfg: Config, text: str, *, label: str) -> SafetyGateAssessment:
    result = await SafetyClassifier(cfg).classify(text, label=label)
    return SafetyGateAssessment(result=result, action=result.action(cfg))


def append_safety_review(body: str, assessment: SafetyGateAssessment) -> str:
    return (
        body
        + "\n\n## Safety review\n"
        + f"- Action: `{assessment.action}`\n"
        + f"- Categories: `{', '.join(assessment.result.categories)}`\n"
        + f"- Rationale: {assessment.result.rationale[:1000]}"
    )
