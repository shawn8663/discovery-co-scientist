"""Robin-style therapeutic discovery entities."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DiscoveryWorkflow = Literal["general_hypothesis", "therapeutic_discovery"]
AnalysisKind = Literal["flow_cytometry", "rnaseq", "tabular"]


class AssayProposal(BaseModel):
    id: str
    session_id: str
    created_at: datetime
    round_index: int = 1
    strategy_name: str
    reasoning: str
    artifact_path: str
    rank_score: float | None = None
    state: str = "proposed"


class AssayEvaluation(BaseModel):
    id: str
    assay_id: str
    session_id: str
    created_at: datetime
    overview: str
    biomedical_evidence: str
    previous_use: str
    overall_evaluation: str
    artifact_path: str


class TherapeuticCandidate(BaseModel):
    id: str
    session_id: str
    assay_id: str | None = None
    created_at: datetime
    round_index: int = 1
    candidate: str
    hypothesis: str
    reasoning: str
    artifact_path: str
    rank_score: float | None = None
    state: str = "proposed"


class TherapeuticCandidateEvaluation(BaseModel):
    id: str
    candidate_id: str
    session_id: str
    created_at: datetime
    overview: str
    therapeutic_history: str
    mechanism_of_action: str
    expected_effect: str
    overall_evaluation: str
    artifact_path: str


class AnalysisRun(BaseModel):
    id: str
    session_id: str
    created_at: datetime
    kind: AnalysisKind
    dataset_artifact_ids: list[str] = Field(default_factory=list)
    trajectories: int = 3
    summary: str
    artifact_path: str


class ExperimentInsight(BaseModel):
    id: str
    session_id: str
    analysis_run_id: str
    created_at: datetime
    summary: str
    positive_hits: list[str] = Field(default_factory=list)
    negative_hits: list[str] = Field(default_factory=list)
    suggested_mechanisms: list[str] = Field(default_factory=list)
    follow_up_assays: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    artifact_path: str
