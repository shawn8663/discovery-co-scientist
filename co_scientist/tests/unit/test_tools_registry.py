"""Smoke tests for tool registry + science-skills bridge parsing."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from co_scientist.tools.base import ToolCtx
from co_scientist.tools.registry import ToolRegistry
from co_scientist.tools.science_skills import ScienceSkillTool, discover_skills, parse_skill_md


def test_registry_discovers_builtins(tmp_cfg) -> None:
    """web_search needs a TAVILY/BRAVE key; the others are always available."""
    tmp_cfg.secrets.TAVILY_API_KEY = "sk-fake"
    reg = ToolRegistry(tmp_cfg).discover()
    names = {t.name for t in reg.all()}
    assert {"web_search", "web_fetch", "pubmed_search", "arxiv_search", "europe_pmc_search"} <= names


def test_web_search_skipped_when_no_search_api_key(tmp_cfg) -> None:
    """Without a Tavily/Brave key the model would only see a tool that returns
    errors; small models tend to abort instead of falling back to PubMed.
    Auto-skip the registration to remove that footgun."""
    tmp_cfg.secrets.TAVILY_API_KEY = ""
    tmp_cfg.secrets.BRAVE_API_KEY = ""
    reg = ToolRegistry(tmp_cfg).discover()
    names = {t.name for t in reg.all()}
    assert "web_search" not in names
    # Other literature tools still available.
    assert "pubmed_search" in names
    assert "europe_pmc_search" in names


def test_agent_allowlist_resolution(tmp_cfg) -> None:
    tmp_cfg.secrets.TAVILY_API_KEY = "sk-fake"
    reg = ToolRegistry(tmp_cfg).discover()
    assert len(reg.tools_for("ranking")) == 0
    assert len(reg.tools_for("proximity")) == 0
    # generation/reflection/evolution get all built-in literature tools
    for agent in ("generation", "reflection", "evolution"):
        ts = {t.name for t in reg.tools_for(agent)}
        assert "web_search" in ts
        assert "pubmed_search" in ts


def test_skill_md_parsing(tmp_path: Path, tmp_cfg, monkeypatch) -> None:
    skills_root = tmp_path / "skills"
    sk = skills_root / "my_test_skill"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: my_test_skill
            description: A short description for the LLM
            category: retrieval
            entrypoint: scripts/run.py
            required_files: ["input.csv"]
            required_secrets: ["NCBI_API_KEY"]
            network_access: true
            write_scope: run_workspace
            expected_outputs: ["json"]
            safety_level: trusted_local
            timeout_seconds: 30
            ---

            More detail follows.
            """
        )
    )
    (sk / "scripts" / "run.py").write_text("print('{}')\n")

    meta = parse_skill_md(sk)
    assert meta is not None
    assert meta.name == "my_test_skill"
    assert meta.description.startswith("A short description")
    assert meta.entrypoint is not None and meta.entrypoint.name == "run.py"
    assert meta.timeout_seconds == 30
    assert meta.category == "retrieval"
    assert meta.required_files == ["input.csv"]
    assert meta.requires_keys == ["NCBI_API_KEY"]
    assert meta.network_access is True
    assert meta.expected_outputs == ["json"]

    # discover_skills walks <science_skills.path>/skills
    monkeypatch.setattr(tmp_cfg.science_skills, "path", str(tmp_path))
    discovered = discover_skills(tmp_cfg)
    assert any(d.name == "my_test_skill" for d in discovered)


def test_skill_md_without_front_matter_still_parses(tmp_path: Path) -> None:
    sk = tmp_path / "raw_skill"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text("# Raw skill\n\nThis describes what it does.\n")
    (sk / "scripts" / "main.py").write_text("print('{}')\n")
    meta = parse_skill_md(sk)
    assert meta is not None
    assert meta.name == "raw_skill"
    assert meta.entrypoint is not None and meta.entrypoint.name == "main.py"


@pytest.mark.asyncio
async def test_science_skill_call_captures_provenance(tmp_path: Path, tmp_cfg) -> None:
    sk = tmp_path / "skill"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: provenance_skill
            description: Emits JSON
            category: analysis
            entrypoint: scripts/run.py
            expected_outputs: ["summary_json"]
            ---
            """
        )
    )
    (sk / "scripts" / "run.py").write_text(
        "import json, sys\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'ok': True, 'payload': payload}))\n"
    )
    meta = parse_skill_md(sk)
    assert meta is not None
    tool = ScienceSkillTool(tmp_cfg, meta)

    result = await tool.call(
        {"args": {"x": 1}},
        ToolCtx(cfg=tmp_cfg, session_id="ses_tool", run_id="run_1"),
    )

    assert result.is_error is False
    assert result.content["result"]["ok"] is True
    provenance = result.content["provenance"]
    assert provenance["run_id"] == "run_1"
    assert provenance["category"] == "analysis"
    assert Path(provenance["cwd"], "provenance.json").exists()
    workspace_entries = tmp_cfg.data_dir / "workspaces" / "ses_tool" / "manifest.json"
    assert workspace_entries.exists()


@pytest.mark.asyncio
async def test_science_skill_approval_required_policy_blocks_risky_tool(tmp_path: Path, tmp_cfg) -> None:
    sk = tmp_path / "skill"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: network_skill
            description: Needs network
            entrypoint: scripts/run.py
            network_access: true
            requires_approval: true
            ---
            """
        )
    )
    (sk / "scripts" / "run.py").write_text("print('{}')\n")
    tmp_cfg.science_skills.execution_policy = "approval_required"
    meta = parse_skill_md(sk)
    assert meta is not None

    result = await ScienceSkillTool(tmp_cfg, meta).call({}, ToolCtx(cfg=tmp_cfg, run_id="run_2"))

    assert result.is_error is True
    assert result.content["approval_required"]["network_access"] is True
