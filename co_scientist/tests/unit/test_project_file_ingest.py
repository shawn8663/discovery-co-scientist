"""Project-file ingestion before initial generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from co_scientist.agents.supervisor import Supervisor
from co_scientist.models import ResearchPlan
from co_scientist.orchestrator.termination import StopReason
from co_scientist.storage.repos import tasks as task_repo
from co_scientist.workspace import ScientistWorkspace
from co_scientist.workspace.ingest import collect_project_files, ingest_project_files


async def test_ingest_project_files_copies_and_indexes_pdf(tmp_path: Path, tmp_cfg) -> None:
    pdf = tmp_path / "references" / "paper.pdf"
    pdf.parent.mkdir()
    _write_minimal_text_pdf(pdf, "Phe-CA RORgt Th17 inflammation")

    artifacts = ingest_project_files(tmp_cfg, "ses_ingest", [pdf])

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.kind == "project_file"
    assert artifact.title == "paper.pdf"
    assert Path(artifact.path).is_file()
    assert Path(artifact.path).parent == tmp_cfg.data_dir / "workspaces" / "ses_ingest" / "uploads"
    assert artifact.metadata["content_type"] == "application/pdf"
    assert artifact.metadata["indexed"] is True
    assert (tmp_cfg.data_dir / "cache" / "local_pdfs").exists()


def test_collect_project_files_expands_directories_to_pdfs(tmp_path: Path) -> None:
    direct = tmp_path / "direct.pdf"
    nested = tmp_path / "refs" / "nested.pdf"
    ignored = tmp_path / "refs" / "notes.txt"
    direct.write_text("direct")
    nested.parent.mkdir()
    nested.write_text("nested")
    ignored.write_text("notes")

    files = collect_project_files(files=[direct], dirs=[nested.parent])

    assert files == [direct.resolve(), nested.resolve()]


async def test_supervisor_ingests_project_files_before_generation_queue(tmp_path: Path, tmp_cfg) -> None:
    pdf = tmp_path / "seed.pdf"
    _write_minimal_text_pdf(pdf, "local source material")

    class CapturingSupervisor(Supervisor):
        async def _check_research_goal_safety(self, goal: str) -> None:
            return None

        async def _parse_goal(self, deps, session, goal: str, preferences_text: str | None):
            return ResearchPlan(objective=goal)

        async def _main_loop(self, conn, deps, session, tracker):
            self.artifacts_seen = ScientistWorkspace(self.cfg, session.id).list()
            self.pending_tasks_seen = await task_repo.count_by_status(conn, session.id)
            return StopReason.IDLE

        async def _finalize(self, conn, deps, session, stop_reason) -> None:
            return None

    sup = CapturingSupervisor(tmp_cfg)
    with patch("co_scientist.agents.supervisor.get_provider", return_value=MagicMock()):
        await sup.run_session("Use local PDFs", project_files=[pdf], n_initial=2)

    assert [a.title for a in sup.artifacts_seen] == ["seed.pdf"]
    assert sup.pending_tasks_seen["pending"] == 2


def _write_minimal_text_pdf(path: Path, text: str) -> None:
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
    path.write_bytes(body)
