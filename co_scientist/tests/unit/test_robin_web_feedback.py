"""Web feedback hooks for Robin assay and candidate decisions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from co_scientist.models import AssayProposal, ResearchPlan, Session, TherapeuticCandidate
from co_scientist.storage.repos import feedback as fb_repo
from co_scientist.storage.repos import robin as robin_repo
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.web.app import create_app


def _now() -> datetime:
    return datetime.now(UTC)


async def test_web_feedback_can_pin_assay_and_reject_candidate(tmp_cfg, conn) -> None:
    session = Session(
        id="ses_robin_web_feedback",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        workflow="therapeutic_discovery",
        research_goal="Discover therapeutics for dry AMD",
        research_plan=ResearchPlan(objective="Discover therapeutics for dry AMD"),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    assay = AssayProposal(
        id="assay_web_feedback",
        session_id=session.id,
        created_at=_now(),
        strategy_name="RPE phagocytosis assay",
        reasoning="Functional disease model.",
        artifact_path=f"artifacts/{session.id}/robin/assay.json",
    )
    candidate = TherapeuticCandidate(
        id="candidate_web_feedback",
        session_id=session.id,
        assay_id=assay.id,
        created_at=_now(),
        candidate="Example ROCK inhibitor",
        hypothesis="ROCK inhibition improves RPE function.",
        reasoning="Mechanistic candidate for the selected assay.",
        artifact_path=f"artifacts/{session.id}/robin/candidate.json",
    )
    await robin_repo.insert_assay(conn, assay)
    await robin_repo.insert_candidate(conn, candidate)

    client = TestClient(create_app(tmp_cfg))
    assay_response = client.post(
        f"/api/sessions/{session.id}/feedback",
        data={
            "kind": "pin",
            "target_type": "assay",
            "target_id": assay.id,
            "text": "Expert wants this assay carried forward.",
        },
    )
    candidate_response = client.post(
        f"/api/sessions/{session.id}/feedback",
        data={
            "kind": "rejection",
            "target_type": "candidate",
            "target_id": candidate.id,
            "text": "Expert rejects this candidate due to tolerability concerns.",
        },
    )

    assert assay_response.status_code == 200
    assert candidate_response.status_code == 200
    assert (await robin_repo.fetch_assay(conn, assay.id)).state == "pinned"
    assert (await robin_repo.fetch_candidate(conn, candidate.id)).state == "rejected"
    assay_feedback = await fb_repo.active_for_session(conn, session.id, assay.id)
    candidate_feedback = await fb_repo.active_for_session(conn, session.id, candidate.id)
    assert [(f.kind, f.target_id) for f in assay_feedback] == [("pin", assay.id)]
    assert [(f.kind, f.target_id) for f in candidate_feedback] == [
        ("rejection", candidate.id)
    ]


async def test_therapeutic_dashboard_exposes_assay_and_candidate_decision_controls(
    tmp_cfg, conn
) -> None:
    session = Session(
        id="ses_robin_web_controls",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        workflow="therapeutic_discovery",
        research_goal="Discover therapeutics for dry AMD",
        research_plan=ResearchPlan(objective="Discover therapeutics for dry AMD"),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    assay = AssayProposal(
        id="assay_web_controls",
        session_id=session.id,
        created_at=_now(),
        strategy_name="RPE phagocytosis assay",
        reasoning="Functional disease model.",
        artifact_path=f"artifacts/{session.id}/robin/assay.json",
    )
    candidate = TherapeuticCandidate(
        id="candidate_web_controls",
        session_id=session.id,
        assay_id=assay.id,
        created_at=_now(),
        candidate="Example ROCK inhibitor",
        hypothesis="ROCK inhibition improves RPE function.",
        reasoning="Mechanistic candidate for the selected assay.",
        artifact_path=f"artifacts/{session.id}/robin/candidate.json",
    )
    await robin_repo.insert_assay(conn, assay)
    await robin_repo.insert_candidate(conn, candidate)

    response = TestClient(create_app(tmp_cfg)).get(f"/sessions/{session.id}")

    assert response.status_code == 200
    assert 'name="target_type" value="assay"' in response.text
    assert 'name="target_type" value="candidate"' in response.text
    assert f'name="target_id" value="{assay.id}"' in response.text
    assert f'name="target_id" value="{candidate.id}"' in response.text
    assert 'name="kind" value="pin"' in response.text
    assert 'name="kind" value="rejection"' in response.text
