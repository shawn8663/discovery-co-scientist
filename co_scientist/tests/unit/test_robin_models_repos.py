"""Storage and model coverage for Robin-style therapeutic discovery entities."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from co_scientist import ids
from co_scientist.models import (
    AnalysisRun,
    AssayEvaluation,
    AssayProposal,
    ExperimentInsight,
    ResearchPlan,
    Session,
    TherapeuticCandidate,
    TherapeuticCandidateEvaluation,
)
from co_scientist.storage.repos import robin as robin_repo
from co_scientist.storage.repos import sessions as sess_repo


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_session(conn, sid: str = "ses_robin") -> Session:
    s = Session(
        id=sid,
        created_at=_now(),
        updated_at=_now(),
        status="running",
        workflow="therapeutic_discovery",
        research_goal="Discover therapeutics for dry AMD",
        research_plan=ResearchPlan(objective="Discover therapeutics for dry AMD"),
        config_snapshot={},
        budget_tokens=10000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, s)
    return s


@pytest.mark.asyncio
async def test_session_workflow_roundtrip_defaults_and_explicit(conn) -> None:
    general = Session(
        id="ses_general",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        research_goal="Explain mechanism X",
        research_plan=ResearchPlan(objective="Explain mechanism X"),
        config_snapshot={},
        budget_tokens=10000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, general)
    fetched_general = await sess_repo.fetch(conn, general.id)
    assert fetched_general is not None
    assert fetched_general.workflow == "general_hypothesis"

    therapeutic = await _make_session(conn, sid="ses_therapeutic")
    fetched_therapeutic = await sess_repo.fetch(conn, therapeutic.id)
    assert fetched_therapeutic is not None
    assert fetched_therapeutic.workflow == "therapeutic_discovery"


@pytest.mark.asyncio
async def test_robin_entities_roundtrip_and_list_by_session(conn) -> None:
    s = await _make_session(conn)
    assay = AssayProposal(
        id=ids.artifact_id(),
        session_id=s.id,
        created_at=_now(),
        round_index=1,
        strategy_name="RPE phagocytosis enhancement assay",
        reasoning="Models a disease-relevant functional endpoint.",
        artifact_path=f"artifacts/{s.id}/robin/assay.json",
    )
    assert await robin_repo.insert_assay(conn, assay) is True
    assert await robin_repo.insert_assay(conn, assay) is False

    evaluation = AssayEvaluation(
        id=ids.artifact_id(),
        assay_id=assay.id,
        session_id=s.id,
        created_at=_now(),
        overview="Measures uptake of fluorescent outer segments.",
        biomedical_evidence="RPE phagocytic dysfunction is disease relevant.",
        previous_use="Used in RPE drug discovery screens.",
        overall_evaluation="Strong functional assay.",
        artifact_path=f"artifacts/{s.id}/robin/assay_eval.json",
    )
    assert await robin_repo.insert_assay_evaluation(conn, evaluation) is True

    candidate = TherapeuticCandidate(
        id=ids.artifact_id(),
        session_id=s.id,
        assay_id=assay.id,
        created_at=_now(),
        round_index=1,
        candidate="Ripasudil",
        hypothesis="ROCK inhibition enhances RPE phagocytosis.",
        reasoning="Commercially available ocular ROCK inhibitor with plausible mechanism.",
        artifact_path=f"artifacts/{s.id}/robin/candidate.json",
    )
    assert await robin_repo.insert_candidate(conn, candidate) is True

    candidate_eval = TherapeuticCandidateEvaluation(
        id=ids.artifact_id(),
        candidate_id=candidate.id,
        session_id=s.id,
        created_at=_now(),
        overview="Clinically used ROCK inhibitor.",
        therapeutic_history="Approved for ocular use in some jurisdictions.",
        mechanism_of_action="Inhibits ROCK signaling.",
        expected_effect="Increased actin remodeling and phagocytosis.",
        overall_evaluation="Promising repurposing candidate.",
        artifact_path=f"artifacts/{s.id}/robin/candidate_eval.json",
    )
    assert await robin_repo.insert_candidate_evaluation(conn, candidate_eval) is True

    analysis = AnalysisRun(
        id=ids.artifact_id(),
        session_id=s.id,
        created_at=_now(),
        kind="flow_cytometry",
        dataset_artifact_ids=["dataset_1"],
        trajectories=3,
        summary="Three trajectories agree that ripasudil increased MFI.",
        artifact_path=f"artifacts/{s.id}/analysis/run.json",
    )
    assert await robin_repo.insert_analysis_run(conn, analysis) is True

    insight = ExperimentInsight(
        id=ids.artifact_id(),
        session_id=s.id,
        analysis_run_id=analysis.id,
        created_at=_now(),
        summary="ROCK inhibition is a hit class.",
        positive_hits=["Ripasudil"],
        negative_hits=["Vehicle"],
        suggested_mechanisms=["Cytoskeletal remodeling"],
        follow_up_assays=["Dose-response in stem-cell derived RPE"],
        constraints=["Avoid cytotoxic concentrations"],
        artifact_path=f"artifacts/{s.id}/insights/insight.json",
    )
    assert await robin_repo.insert_experiment_insight(conn, insight) is True

    assert [a.id for a in await robin_repo.list_assays(conn, s.id)] == [assay.id]
    assert [e.id for e in await robin_repo.list_assay_evaluations(conn, s.id)] == [evaluation.id]
    assert [c.id for c in await robin_repo.list_candidates(conn, s.id)] == [candidate.id]
    assert [e.id for e in await robin_repo.list_candidate_evaluations(conn, s.id)] == [
        candidate_eval.id
    ]
    assert [r.id for r in await robin_repo.list_analysis_runs(conn, s.id)] == [analysis.id]
    assert [i.id for i in await robin_repo.list_experiment_insights(conn, s.id)] == [insight.id]


@pytest.mark.asyncio
async def test_robin_human_override_updates_assay_and_candidate_state(conn) -> None:
    s = await _make_session(conn, sid="ses_robin_override")
    assay = AssayProposal(
        id="assay_override",
        session_id=s.id,
        created_at=_now(),
        round_index=1,
        strategy_name="Complement stress rescue assay",
        reasoning="Tests a disease-relevant inflammatory mechanism.",
        artifact_path=f"artifacts/{s.id}/robin/assay.json",
    )
    candidate = TherapeuticCandidate(
        id="cand_override",
        session_id=s.id,
        assay_id=assay.id,
        created_at=_now(),
        round_index=1,
        candidate="Example complement modulator",
        hypothesis="Complement modulation rescues stressed RPE.",
        reasoning="Mechanistically aligned with the assay.",
        artifact_path=f"artifacts/{s.id}/robin/candidate.json",
    )
    await robin_repo.insert_assay(conn, assay)
    await robin_repo.insert_candidate(conn, candidate)

    await robin_repo.set_assay_state(conn, assay.id, "pinned")
    await robin_repo.set_candidate_state(conn, candidate.id, "rejected")

    assert (await robin_repo.fetch_assay(conn, assay.id)).state == "pinned"
    assert (await robin_repo.fetch_candidate(conn, candidate.id)).state == "rejected"
