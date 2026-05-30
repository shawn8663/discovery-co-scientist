"""Search PDFs registered in the local scientist workspace."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from ..config import Config
from ..workspace import ScientistWorkspace, WorkspaceArtifact
from .base import ToolCtx, ToolResult


class LocalPDFSearchTool:
    name = "local_pdf_search"
    description = (
        "Search PDF files registered in the current local workspace. Use this for uploaded "
        "papers, supplemental material, and local project PDFs before broad web retrieval."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "max_chars": {"type": "integer", "minimum": 200, "maximum": 12000, "default": 2000},
        },
        "required": ["query"],
    }

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    async def call(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult:
        t0 = time.monotonic()
        if ctx.session_id is None:
            return ToolResult(is_error=True, error_message="local_pdf_search requires session context")
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(is_error=True, error_message="empty query")
        limit = int(args.get("max_results") or 5)
        max_chars = int(args.get("max_chars") or 2000)
        workspace = ScientistWorkspace(self._cfg, ctx.session_id)
        artifacts = [a for a in workspace.list() if _looks_like_pdf(a)]
        results: list[dict[str, Any]] = []
        cache_hits = 0
        cache_misses = 0
        parse_errors = 0
        for artifact in artifacts:
            path = Path(artifact.path)
            if not path.is_file():
                continue
            try:
                indexed, cache_hit = _read_or_index_pdf(self._cfg, artifact, path)
            except Exception:
                parse_errors += 1
                continue
            if cache_hit:
                cache_hits += 1
            else:
                cache_misses += 1
            score = _score(indexed["text"], query)
            if score <= 0:
                continue
            snippet = _snippet(indexed["text"], query, max_chars=max_chars)
            results.append(
                {
                    "title": artifact.title or path.name,
                    "path": str(path),
                    "text": snippet,
                    "score": score,
                    "pages": indexed.get("pages", 0),
                    "artifact_id": artifact.id,
                }
            )
        results.sort(key=lambda r: r["score"], reverse=True)
        payload = {"query": query, "n": len(results[:limit]), "results": results[:limit]}
        return ToolResult(
            content=payload,
            duration_ms=int((time.monotonic() - t0) * 1000),
            result_bytes=len(json.dumps(payload)),
            metadata={
                "retrieval_source": self.name,
                "cache_hit": cache_misses == 0 and cache_hits > 0,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "parse_errors": parse_errors,
            },
        )


def _looks_like_pdf(artifact: WorkspaceArtifact) -> bool:
    path = artifact.path.lower()
    content_type = str(artifact.metadata.get("content_type", "")).lower()
    return path.endswith(".pdf") or content_type == "application/pdf"


def _read_or_index_pdf(cfg: Config, artifact: WorkspaceArtifact, path: Path) -> tuple[dict[str, Any], bool]:
    cache_dir = cfg.data_dir / "cache" / "local_pdfs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stat = path.stat()
    key = hashlib.sha256(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    cache_path = cache_dir / f"{key}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), True
        except json.JSONDecodeError:
            pass
    pages: list[str] = []
    reader = PdfReader(str(path))
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    payload = {
        "artifact_id": artifact.id,
        "path": str(path),
        "pages": len(pages),
        "text": "\n\n".join(pages),
    }
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(cache_path)
    return payload, False


def _score(text: str, query: str) -> int:
    haystack = text.lower()
    terms = [term for term in query.lower().split() if term]
    return sum(haystack.count(term) for term in terms)


def _snippet(text: str, query: str, *, max_chars: int) -> str:
    lowered = text.lower()
    first_positions = [lowered.find(term) for term in query.lower().split() if lowered.find(term) >= 0]
    start = max(min(first_positions) - 300, 0) if first_positions else 0
    return text[start:start + max_chars]
