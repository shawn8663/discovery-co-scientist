"""Project-file ingestion for session workspaces."""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path

from ..config import Config
from .manifest import ScientistWorkspace, WorkspaceArtifact


def collect_project_files(
    *,
    files: list[Path] | None = None,
    dirs: list[Path] | None = None,
) -> list[Path]:
    """Expand direct files and directories into a stable file list.

    Directory inputs currently collect PDFs recursively. Direct file inputs are
    kept regardless of suffix so users can attach notes or datasets too.
    """
    out: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        out.append(resolved)

    for path in files or []:
        if path.is_file():
            _add(path)

    for directory in dirs or []:
        root = directory.expanduser().resolve()
        if not root.is_dir():
            continue
        for pdf in sorted(root.rglob("*.pdf")):
            if pdf.is_file():
                _add(pdf)
    return out


def ingest_project_files(
    cfg: Config,
    session_id: str,
    project_files: list[Path],
) -> list[WorkspaceArtifact]:
    """Copy project files into a session workspace and register artifacts."""
    workspace = ScientistWorkspace(cfg, session_id)
    workspace.ensure()
    uploads = workspace.root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    artifacts: list[WorkspaceArtifact] = []
    for source in project_files:
        if not source.is_file():
            continue
        dest = _unique_dest(uploads, source.name)
        shutil.copy2(source, dest)
        content_type = mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
        artifact = workspace.add_artifact(
            kind="project_file",
            path=dest,
            title=source.name,
            provenance={"source": "session_start", "original_path": str(source.resolve())},
            metadata={
                "content_type": content_type,
                "size_bytes": dest.stat().st_size,
            },
        )
        if _is_pdf(dest, content_type):
            _index_pdf(cfg, artifact, dest)
            _replace_artifact(workspace, artifact)
        artifacts.append(artifact)
    return artifacts


def _unique_dest(directory: Path, filename: str) -> Path:
    candidate = directory / Path(filename).name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    i = 2
    while True:
        next_candidate = directory / f"{stem}-{i}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        i += 1


def _is_pdf(path: Path, content_type: str) -> bool:
    return path.suffix.lower() == ".pdf" or content_type == "application/pdf"


def _index_pdf(cfg: Config, artifact: WorkspaceArtifact, path: Path) -> None:
    from ..tools.local_pdf_search import _read_or_index_pdf

    try:
        _read_or_index_pdf(cfg, artifact, path)
    except Exception as e:
        artifact.metadata["indexed"] = False
        artifact.metadata["parse_error"] = str(e)
    else:
        artifact.metadata["indexed"] = True


def _replace_artifact(workspace: ScientistWorkspace, artifact: WorkspaceArtifact) -> None:
    current = workspace.list()
    updated = [artifact if item.id == artifact.id else item for item in current]
    workspace._write(updated)
