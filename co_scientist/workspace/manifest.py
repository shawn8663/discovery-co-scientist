"""Local augmented-scientist workspace manifest.

The manifest is intentionally lightweight: it gives the local app a stable place
to record project files, retrieved literature, datasets, generated analyses,
drafts, citations, figures, and final publication artifacts without forcing the
rest of the system into a new storage backend.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import Config
from ..ids import artifact_id

ArtifactKind = Literal[
    "project_file",
    "retrieved_literature",
    "dataset",
    "analysis",
    "draft",
    "citation",
    "figure",
    "final_publication",
    "tool_run",
]


class WorkspaceArtifact(BaseModel):
    id: str
    kind: ArtifactKind
    path: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    title: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScientistWorkspace:
    """A per-session artifact manifest for the local researcher app."""

    def __init__(self, cfg: Config, session_id: str) -> None:
        self.cfg = cfg
        self.session_id = session_id
        self.root = cfg.data_dir / "workspaces" / session_id
        self.manifest_path = self.root / "manifest.json"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            self._write([])

    def list(self) -> list[WorkspaceArtifact]:
        self.ensure()
        try:
            raw = json.loads(self.manifest_path.read_text())
        except json.JSONDecodeError:
            raw = []
        return [WorkspaceArtifact.model_validate(x) for x in raw]

    def add_artifact(
        self,
        *,
        kind: ArtifactKind,
        path: str | Path,
        title: str = "",
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceArtifact:
        self.ensure()
        resolved = self._normalize_path(path)
        artifact = WorkspaceArtifact(
            id=artifact_id(),
            kind=kind,
            path=resolved,
            title=title,
            provenance=provenance or {},
            metadata=metadata or {},
        )
        current = self.list()
        current.append(artifact)
        self._write(current)
        return artifact

    def _normalize_path(self, path: str | Path) -> str:
        p = Path(path)
        if p.is_absolute():
            return str(p)
        return str((self.root / p).resolve())

    def _write(self, artifacts: list[WorkspaceArtifact]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [a.model_dump(mode="json") for a in artifacts]
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
