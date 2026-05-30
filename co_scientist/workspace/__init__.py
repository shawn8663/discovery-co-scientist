"""Augmented scientist workspace primitives."""

from .manifest import ArtifactKind, ScientistWorkspace, WorkspaceArtifact
from .publication import write_claims_table_artifact, write_publication_draft_artifact

__all__ = [
    "ArtifactKind",
    "ScientistWorkspace",
    "WorkspaceArtifact",
    "write_claims_table_artifact",
    "write_publication_draft_artifact",
]
