"""Smoke tests for tool registry + science-skills bridge parsing."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import ClassVar

import pytest

from co_scientist.tools.base import ToolCtx
from co_scientist.tools.registry import ToolRegistry
from co_scientist.tools.science_skills import (
    ScienceSkillTool,
    _sanitized_env,
    discover_skills,
    parse_skill_md,
)
from co_scientist.workspace import ScientistWorkspace


def test_registry_discovers_builtins(tmp_cfg) -> None:
    """web_search needs a TAVILY/BRAVE key; the others are always available."""
    tmp_cfg.secrets.TAVILY_API_KEY = "sk-fake"
    reg = ToolRegistry(tmp_cfg).discover()
    names = {t.name for t in reg.all()}
    assert {
        "web_search",
        "web_fetch",
        "pubmed_search",
        "arxiv_search",
        "europe_pmc_search",
        "openalex_search",
        "clinical_trials_search",
    } <= names


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
    assert "openalex_search" in names
    assert "clinical_trials_search" in names


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
            required_secrets: ["NCBI_API_KEY"]
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
    tmp_cfg.secrets.NCBI_API_KEY = "ncbi-secret-value"

    result = await tool.call(
        {"args": {"x": 1}},
        ToolCtx(cfg=tmp_cfg, session_id="ses_tool", run_id="run_1"),
    )

    assert result.is_error is False
    assert result.content["result"]["ok"] is True
    provenance = result.content["provenance"]
    assert provenance["run_id"] == "run_1"
    assert provenance["category"] == "analysis"
    provenance_path = Path(provenance["cwd"], "provenance.json")
    assert provenance_path.exists()
    provenance_text = provenance_path.read_text()
    assert '"secrets_available": [\n    "NCBI_API_KEY"\n  ]' in provenance_text
    assert "ncbi-secret-value" not in provenance_text
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


@pytest.mark.asyncio
async def test_science_skill_requires_visible_approval_for_declared_risk(
    tmp_path: Path, tmp_cfg
) -> None:
    sk = tmp_path / "skill"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: declared_risky_skill
            description: Needs visible approval
            entrypoint: scripts/run.py
            network_access: true
            write_scope: project
            requires_approval: true
            ---
            """
        )
    )
    (sk / "scripts" / "run.py").write_text("print('{}')\n")
    meta = parse_skill_md(sk)
    assert meta is not None

    result = await ScienceSkillTool(tmp_cfg, meta).call({}, ToolCtx(cfg=tmp_cfg, run_id="run_risky"))

    assert result.is_error is True
    approval = result.content["approval_required"]
    assert approval["skill"] == "declared_risky_skill"
    assert approval["network_access"] is True
    assert approval["write_scope"] == "project"
    assert approval["requires_approval"] is True


@pytest.mark.asyncio
async def test_science_skill_approved_risky_run_executes(tmp_path: Path, tmp_cfg) -> None:
    sk = tmp_path / "skill"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: approved_risky_skill
            description: Runs after approval
            entrypoint: scripts/run.py
            network_access: true
            requires_approval: true
            ---
            """
        )
    )
    (sk / "scripts" / "run.py").write_text("print('{\"ok\": true}')\n")
    meta = parse_skill_md(sk)
    assert meta is not None

    result = await ScienceSkillTool(tmp_cfg, meta).call(
        {},
        ToolCtx(
            cfg=tmp_cfg,
            run_id="run_approved",
            extra={"approved_science_skill_runs": ["approved_risky_skill"]},
        ),
    )

    assert result.is_error is False
    assert result.content["result"] == {"ok": True}


def test_science_skill_env_injects_only_declared_secrets(monkeypatch, tmp_cfg) -> None:
    monkeypatch.setenv("NCBI_API_KEY", "ncbi-env")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env")
    tmp_cfg.secrets.OPENALEX_API_KEY = "openalex-cfg"

    env = _sanitized_env(tmp_cfg, ["NCBI_API_KEY", "OPENALEX_API_KEY"])

    assert env["NCBI_API_KEY"] == "ncbi-env"
    assert env["OPENALEX_API_KEY"] == "openalex-cfg"
    assert "OPENAI_API_KEY" not in env


@pytest.mark.asyncio
async def test_science_skill_missing_declared_secret_fails_before_execution(
    tmp_path: Path, tmp_cfg, monkeypatch
) -> None:
    sk = tmp_path / "skill"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: needs_secret
            description: Needs a secret
            entrypoint: scripts/run.py
            required_secrets: ["CO_SCIENTIST_TEST_MISSING_KEY"]
            ---
            """
        )
    )
    marker = tmp_path / "should_not_exist"
    (sk / "scripts" / "run.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    monkeypatch.delenv("CO_SCIENTIST_TEST_MISSING_KEY", raising=False)
    meta = parse_skill_md(sk)
    assert meta is not None

    result = await ScienceSkillTool(tmp_cfg, meta).call({}, ToolCtx(cfg=tmp_cfg, run_id="run_missing"))

    assert result.is_error is True
    assert result.content == {"missing_required_secrets": ["CO_SCIENTIST_TEST_MISSING_KEY"]}
    assert not marker.exists()


@pytest.mark.asyncio
async def test_retrieval_tool_call_saves_literature_workspace_artifact(tmp_cfg) -> None:
    class FakeRetrievalTool:
        name = "pubmed_search"
        description = "fake retrieval"
        input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

        async def call(self, args, ctx):
            from co_scientist.tools.base import ToolResult

            return ToolResult(
                content={
                    "query": args["query"],
                    "n": 1,
                    "results": [
                        {
                            "title": "Paper title",
                            "url": "https://example.test/paper",
                            "doi": "10.123/example",
                            "year": 2024,
                        }
                    ],
                },
                metadata={"retrieval_source": self.name, "cache_hit": False},
            )

    registry = ToolRegistry(tmp_cfg)
    registry._register(FakeRetrievalTool())

    result = await registry.call(
        "pubmed_search",
        {"query": "AML LAT1"},
        ToolCtx(cfg=tmp_cfg, session_id="ses_lit", run_id="run_lit"),
    )

    assert result.is_error is False
    artifacts = ScientistWorkspace(tmp_cfg, "ses_lit").list()
    [artifact] = [a for a in artifacts if a.kind == "retrieved_literature"]
    assert artifact.metadata["source"] == "pubmed_search"
    assert artifact.metadata["query"] == "AML LAT1"
    assert artifact.metadata["cache_hit"] is False
    assert artifact.metadata["citation_metadata"] == [
        {
            "title": "Paper title",
            "url": "https://example.test/paper",
            "doi": "10.123/example",
            "year": 2024,
        }
    ]


@pytest.mark.asyncio
async def test_analysis_and_drafting_skills_create_workspace_artifacts(tmp_path: Path, tmp_cfg) -> None:
    for category, expected_kind in (("analysis", "analysis"), ("drafting", "draft")):
        sk = tmp_path / category
        (sk / "scripts").mkdir(parents=True)
        (sk / "SKILL.md").write_text(
            dedent(
                f"""\
                ---
                name: {category}_skill
                description: Emits JSON
                category: {category}
                entrypoint: scripts/run.py
                expected_outputs: ["json"]
                ---
                """
            )
        )
        (sk / "scripts" / "run.py").write_text("print('{\"ok\": true}')\n")
        meta = parse_skill_md(sk)
        assert meta is not None

        result = await ScienceSkillTool(tmp_cfg, meta).call(
            {"args": {"category": category}},
            ToolCtx(cfg=tmp_cfg, session_id=f"ses_{category}", run_id=f"run_{category}"),
        )

        assert result.is_error is False
        artifacts = ScientistWorkspace(tmp_cfg, f"ses_{category}").list()
        assert any(a.kind == expected_kind for a in artifacts)
        assert any(a.kind == "tool_run" for a in artifacts)


@pytest.mark.asyncio
async def test_science_skill_reuses_cached_successful_run(tmp_path: Path, tmp_cfg) -> None:
    sk = tmp_path / "cached"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: cached_skill
            description: Emits JSON
            category: analysis
            entrypoint: scripts/run.py
            ---
            """
        )
    )
    counter = tmp_path / "counter.txt"
    (sk / "scripts" / "run.py").write_text(
        "import json\n"
        f"from pathlib import Path\np=Path({str(counter)!r})\n"
        "n=int(p.read_text() or '0') if p.exists() else 0\n"
        "p.write_text(str(n+1))\n"
        "print(json.dumps({'count': n+1}))\n"
    )
    meta = parse_skill_md(sk)
    assert meta is not None
    tool = ScienceSkillTool(tmp_cfg, meta)

    first = await tool.call({"args": {"x": 1}}, ToolCtx(cfg=tmp_cfg, run_id="run_first"))
    second = await tool.call({"args": {"x": 1}}, ToolCtx(cfg=tmp_cfg, run_id="run_second"))

    assert first.content["result"] == {"count": 1}
    assert second.content["result"] == {"count": 1}
    assert second.metadata["cached"] is True
    assert second.metadata["resumed_from_run_id"] == "run_first"
    assert counter.read_text() == "1"


@pytest.mark.asyncio
async def test_science_skill_does_not_cache_failed_runs(tmp_path: Path, tmp_cfg) -> None:
    sk = tmp_path / "failed"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: failed_skill
            description: Fails
            entrypoint: scripts/run.py
            ---
            """
        )
    )
    counter = tmp_path / "failed_counter.txt"
    (sk / "scripts" / "run.py").write_text(
        f"from pathlib import Path\np=Path({str(counter)!r})\n"
        "n=int(p.read_text() or '0') if p.exists() else 0\n"
        "p.write_text(str(n+1))\n"
        "raise SystemExit(2)\n"
    )
    meta = parse_skill_md(sk)
    assert meta is not None
    tool = ScienceSkillTool(tmp_cfg, meta)

    first = await tool.call({"args": {"x": 1}}, ToolCtx(cfg=tmp_cfg, run_id="run_fail_1"))
    second = await tool.call({"args": {"x": 1}}, ToolCtx(cfg=tmp_cfg, run_id="run_fail_2"))

    assert first.is_error is True
    assert second.is_error is True
    assert "cached" not in second.metadata
    assert counter.read_text() == "2"


@pytest.mark.asyncio
async def test_science_skill_does_not_cache_timed_out_runs(tmp_path: Path, tmp_cfg) -> None:
    sk = tmp_path / "timeout"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        dedent(
            """\
            ---
            name: timeout_skill
            description: Times out
            entrypoint: scripts/run.py
            timeout_seconds: 1
            ---
            """
        )
    )
    (sk / "scripts" / "run.py").write_text("import time\ntime.sleep(5)\nprint('{}')\n")
    meta = parse_skill_md(sk)
    assert meta is not None
    tool = ScienceSkillTool(tmp_cfg, meta)

    result = await tool.call({"args": {"x": 1}}, ToolCtx(cfg=tmp_cfg, run_id="run_timeout"))

    assert result.is_error is True
    assert "timeout" in (result.error_message or "")
    assert "cached" not in result.metadata
