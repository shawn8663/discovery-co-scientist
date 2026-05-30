"""Web UI coverage for local workspace artifacts."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from co_scientist.models import ResearchPlan, Session
from co_scientist.storage.artifacts import write_json, write_text
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.web.app import create_app
from co_scientist.workspace import ScientistWorkspace


async def test_session_detail_shows_workspace_artifacts(tmp_cfg, conn) -> None:
    session = Session(
        id="ses_web_artifacts",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="running",
        research_goal="Find a safer assay design.",
        research_plan=ResearchPlan(objective="Find a safer assay design."),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    ScientistWorkspace(tmp_cfg, session.id).add_artifact(
        kind="dataset",
        path="inputs/assay.csv",
        title="Assay upload",
        provenance={"source": "upload"},
        metadata={"rows": 42},
    )

    response = TestClient(create_app(tmp_cfg)).get(f"/sessions/{session.id}")

    assert response.status_code == 200
    assert "Workspace artifacts" in response.text
    assert 'id="workspace-artifacts"' in response.text
    assert 'class="artifact-path"' in response.text
    assert 'data-label="Path"' in response.text
    assert "Assay upload" in response.text
    assert "dataset" in response.text
    assert "inputs/assay.csv" in response.text


async def test_overview_shows_withheld_safety_status_and_artifact_link(tmp_cfg, conn) -> None:
    session = Session(
        id="ses_web_safety_overview",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="running",
        research_goal="Find a safer assay design.",
        research_plan=ResearchPlan(objective="Find a safer assay design."),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    overview_path = await write_text(
        tmp_cfg,
        session.id,
        "final",
        "overview",
        ".md",
        "# Research overview withheld\n\nThe generated overview requires safety review.",
    )
    safety_path = await write_json(
        tmp_cfg,
        session.id,
        "final",
        "overview_safety",
        {
            "safety_action": "block",
            "safety_categories": ["dual_use_bio"],
            "safety_confidence": 0.91,
            "safety_rationale": "Needs a human safety review.",
        },
    )
    await sess_repo.set_final_overview(conn, session.id, overview_path)

    response = TestClient(create_app(tmp_cfg)).get(f"/sessions/{session.id}/overview")

    assert response.status_code == 200
    assert "Final overview withheld" in response.text
    assert "Safety rationale artifact" in response.text
    assert f"/sessions/{session.id}/artifact?path={safety_path}" in response.text

    artifact_response = TestClient(create_app(tmp_cfg)).get(
        f"/sessions/{session.id}/artifact?path={safety_path}"
    )
    assert artifact_response.status_code == 200
    assert artifact_response.json()["safety_rationale"] == "Needs a human safety review."
