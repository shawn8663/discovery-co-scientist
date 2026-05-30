"""Tests for the local augmented scientist workspace manifest."""

from __future__ import annotations

from pathlib import Path

from co_scientist.workspace import ScientistWorkspace


def test_workspace_records_project_artifacts(tmp_cfg) -> None:
    ws = ScientistWorkspace(tmp_cfg, "ses_test")

    artifact = ws.add_artifact(
        kind="dataset",
        path=Path("inputs") / "assay.csv",
        title="Assay data",
        provenance={"source": "upload"},
        metadata={"rows": 10},
    )

    loaded = ws.list()
    assert len(loaded) == 1
    assert loaded[0].id == artifact.id
    assert loaded[0].kind == "dataset"
    assert loaded[0].path.endswith("workspaces/ses_test/inputs/assay.csv")
    assert loaded[0].provenance == {"source": "upload"}
    assert ws.manifest_path.exists()
