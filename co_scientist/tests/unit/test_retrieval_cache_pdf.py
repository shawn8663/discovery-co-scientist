"""Retrieval cache and local PDF workspace search tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from co_scientist.tools.base import ToolCtx
from co_scientist.tools.cache import RetrievalCache
from co_scientist.tools.local_pdf_search import LocalPDFSearchTool
from co_scientist.tools.registry import ToolRegistry
from co_scientist.workspace import ScientistWorkspace


def test_retrieval_cache_roundtrips_by_tool_and_args(tmp_cfg) -> None:
    cache = RetrievalCache(tmp_cfg, "ses_cache")

    cache.write("openalex_search", {"query": "AML", "max_results": 2}, {"n": 1})

    assert cache.read("openalex_search", {"max_results": 2, "query": "AML"}) == {"n": 1}
    assert cache.read("openalex_search", {"query": "different"}) is None


def test_registry_exposes_local_pdf_search_to_research_agents(tmp_cfg) -> None:
    reg = ToolRegistry(tmp_cfg).discover()
    assert "local_pdf_search" in {tool.name for tool in reg.all()}
    for agent in ("generation", "reflection", "evolution"):
        assert "local_pdf_search" in {tool.name for tool in reg.tools_for(agent)}


@pytest.mark.asyncio
async def test_local_pdf_search_indexes_workspace_pdf(tmp_path: Path, tmp_cfg) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_minimal_text_pdf(pdf_path, "KIRA6 inhibits IRE1 alpha signaling in AML cells")
    ScientistWorkspace(tmp_cfg, "ses_pdf").add_artifact(
        kind="project_file",
        path=pdf_path,
        title="Uploaded paper",
        metadata={"content_type": "application/pdf"},
    )

    result = await LocalPDFSearchTool(tmp_cfg).call(
        {"query": "IRE1 alpha AML", "max_results": 5},
        ToolCtx(cfg=tmp_cfg, session_id="ses_pdf"),
    )

    assert result.is_error is False
    assert result.content["n"] == 1
    hit = result.content["results"][0]
    assert hit["title"] == "Uploaded paper"
    assert "KIRA6 inhibits" in hit["text"]
    assert (tmp_cfg.data_dir / "cache" / "local_pdfs").exists()
    assert result.metadata["cache_hits"] == 0
    assert result.metadata["cache_misses"] == 1

    cached = await LocalPDFSearchTool(tmp_cfg).call(
        {"query": "IRE1 alpha AML", "max_results": 5},
        ToolCtx(cfg=tmp_cfg, session_id="ses_pdf"),
    )

    assert cached.metadata["cache_hits"] == 1
    assert cached.metadata["cache_misses"] == 0


@pytest.mark.asyncio
async def test_local_pdf_search_requires_session_context(tmp_cfg) -> None:
    result = await LocalPDFSearchTool(tmp_cfg).call(
        {"query": "AML"},
        ToolCtx(cfg=tmp_cfg),
    )

    assert result.is_error is True
    assert "session" in (result.error_message or "").lower()


def test_workspace_rejects_relative_project_file_path_traversal(tmp_cfg) -> None:
    with pytest.raises(ValueError, match="workspace"):
        ScientistWorkspace(tmp_cfg, "ses_pdf").add_artifact(
            kind="project_file",
            path="../outside.pdf",
            title="Outside",
            metadata={"content_type": "application/pdf"},
        )


@pytest.mark.asyncio
async def test_local_pdf_search_skips_unparseable_pdf(tmp_path: Path, tmp_cfg) -> None:
    pdf_path = tmp_path / "corrupted.pdf"
    pdf_path.write_text("not a pdf")
    ScientistWorkspace(tmp_cfg, "ses_bad_pdf").add_artifact(
        kind="project_file",
        path=pdf_path,
        title="Corrupted paper",
        metadata={"content_type": "application/pdf"},
    )

    result = await LocalPDFSearchTool(tmp_cfg).call(
        {"query": "anything", "max_results": 5},
        ToolCtx(cfg=tmp_cfg, session_id="ses_bad_pdf"),
    )

    assert result.is_error is False
    assert result.content["n"] == 0
    assert result.metadata["parse_errors"] == 1


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
