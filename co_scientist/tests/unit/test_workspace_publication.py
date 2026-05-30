"""Workspace helpers for claims tables and publication draft artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from co_scientist.workspace import (
    ScientistWorkspace,
    write_claims_table_artifact,
    write_publication_draft_artifact,
)


def test_write_claims_table_artifact_extracts_review_evidence(tmp_cfg) -> None:
    artifact = write_claims_table_artifact(
        tmp_cfg,
        "ses_claims",
        source_id="rev_1",
        markdown=(
            "# Review\n\n"
            "## Evidence\n"
            "- LAT1 is relevant — https://example.test/lat1\n  > quote\n"
            "- DHODH is relevant — https://example.test/dhodh\n  > quote\n"
        ),
    )

    assert artifact.kind == "citation"
    assert artifact.metadata["n_claims"] == 2
    payload = json.loads(Path(artifact.path).read_text())
    assert payload["claims"][0] == {
        "claim": "LAT1 is relevant",
        "url": "https://example.test/lat1",
        "source_id": "rev_1",
    }


def test_write_publication_draft_artifact_preserves_claim_provenance(tmp_cfg) -> None:
    artifact = write_publication_draft_artifact(
        tmp_cfg,
        "ses_draft",
        title="AML draft",
        outline=["Intro", "Results"],
        claims_table=[{"claim": "LAT1 matters", "url": "https://example.test/lat1"}],
        sections={"abstract": "Text"},
        figures=[{"label": "Fig 1", "path": "figures/f1.png"}],
        reviewer_response="Thanks for the review.",
    )

    assert artifact.kind == "draft"
    assert artifact.metadata == {"n_claims": 1, "n_figures": 1, "n_sections": 1}
    assert artifact.provenance["claims"] == ["https://example.test/lat1"]
    artifacts = ScientistWorkspace(tmp_cfg, "ses_draft").list()
    assert any(a.id == artifact.id for a in artifacts)
