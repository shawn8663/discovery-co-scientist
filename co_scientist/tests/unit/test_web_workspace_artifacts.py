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


async def test_project_file_upload_registers_and_indexes_pdf(tmp_cfg, conn) -> None:
    session = Session(
        id="ses_web_upload",
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

    response = TestClient(create_app(tmp_cfg)).post(
        f"/api/sessions/{session.id}/workspace/upload",
        files={"file": ("paper.pdf", _minimal_pdf_bytes("KIRA6 IRE1 alpha"), "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifact"]["kind"] == "project_file"
    assert payload["artifact"]["title"] == "paper.pdf"
    assert payload["indexed"] is True
    assert (tmp_cfg.data_dir / "cache" / "local_pdfs").exists()

    detail = TestClient(create_app(tmp_cfg)).get(f"/sessions/{session.id}")
    assert "paper.pdf" in detail.text


def _minimal_pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n".encode()
    body += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n".encode()
    body += (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
        + b"startxref\n"
        + str(xref_start).encode()
        + b"\n%%EOF\n"
    )
    return body
