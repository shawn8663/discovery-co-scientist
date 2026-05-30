"""Workspace helpers for citation/claims and publication draft artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config import Config
from ..ids import artifact_id
from .manifest import ScientistWorkspace, WorkspaceArtifact

_EVIDENCE_RE = re.compile(r"^-\s*(?P<claim>.*?)\s+—\s+(?P<url>\S+)", re.MULTILINE)


def write_claims_table_artifact(
    cfg: Config,
    session_id: str,
    *,
    source_id: str,
    markdown: str,
) -> WorkspaceArtifact:
    claims = [
        {"claim": m.group("claim").strip(), "url": m.group("url").strip(), "source_id": source_id}
        for m in _EVIDENCE_RE.finditer(markdown)
    ]
    workspace = ScientistWorkspace(cfg, session_id)
    workspace.ensure()
    path = workspace.root / "citations" / f"{artifact_id()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source_id": source_id, "claims": claims}, indent=2) + "\n")
    return workspace.add_artifact(
        kind="citation",
        path=path,
        title=f"Claims table for {source_id}",
        provenance={"source_id": source_id},
        metadata={"n_claims": len(claims)},
    )


def write_publication_draft_artifact(
    cfg: Config,
    session_id: str,
    *,
    title: str,
    outline: list[str],
    claims_table: list[dict[str, Any]],
    sections: dict[str, str],
    figures: list[dict[str, Any]],
    reviewer_response: str = "",
) -> WorkspaceArtifact:
    workspace = ScientistWorkspace(cfg, session_id)
    workspace.ensure()
    payload = {
        "title": title,
        "outline": outline,
        "claims_table": claims_table,
        "sections": sections,
        "figures": figures,
        "reviewer_response": reviewer_response,
    }
    path = workspace.root / "drafts" / f"{artifact_id()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return workspace.add_artifact(
        kind="draft",
        path=Path(path),
        title=title,
        provenance={"claims": [c.get("url") for c in claims_table if c.get("url")]},
        metadata={
            "n_claims": len(claims_table),
            "n_sections": len(sections),
            "n_figures": len(figures),
        },
    )
