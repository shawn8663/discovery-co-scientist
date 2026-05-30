"""ToolRegistry — discovers and indexes all available tools.

Tools available to each agent are decided by `tools_for(agent, mode)` so we can
restrict what the LLM sees per call (smaller tool list = better tool-use quality).
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Config
from ..ids import artifact_id
from ..workspace import ScientistWorkspace
from .base import Tool, ToolCtx, ToolResult, to_anthropic_tool
from .builtins.arxiv import ArxivSearchTool
from .builtins.clinical_trials import ClinicalTrialsSearchTool
from .builtins.europe_pmc import EuropePMCSearchTool
from .builtins.openalex import OpenAlexSearchTool
from .builtins.pubmed import PubmedSearchTool
from .local_pdf_search import LocalPDFSearchTool
from .science_skills import ScienceSkillTool, discover_skills
from .web_fetch import WebFetchTool
from .web_search import WebSearchTool

# Per-agent tool allowlists. Keys are tool names (or "literature_*" wildcards
# matched explicitly in the resolver).
AGENT_TOOLS: dict[str, set[str]] = {
    "generation": {
        "web_search", "web_fetch",
        "local_pdf_search",
        "pubmed_search", "arxiv_search", "europe_pmc_search",
        "openalex_search", "clinical_trials_search",
        "literature_*",   # any science-skills literature_* tools
    },
    "reflection": {
        "web_search", "web_fetch",
        "local_pdf_search",
        "pubmed_search", "arxiv_search", "europe_pmc_search",
        "openalex_search", "clinical_trials_search",
        "literature_*",
        # code_exec wired in M2
    },
    "ranking": set(),                # no tools mid-debate
    "evolution": {
        "web_search", "web_fetch",
        "local_pdf_search",
        "pubmed_search", "arxiv_search", "europe_pmc_search",
        "openalex_search", "clinical_trials_search",
        "literature_*",
    },
    "proximity": set(),
    "metareview": set(),
}


class ToolRegistry:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._tools: dict[str, Tool] = {}

    def discover(self) -> ToolRegistry:
        # Built-ins
        for t in (
            WebFetchTool(self._cfg),
            LocalPDFSearchTool(self._cfg),
            PubmedSearchTool(self._cfg),
            ArxivSearchTool(),
            EuropePMCSearchTool(),
            OpenAlexSearchTool(),
            ClinicalTrialsSearchTool(),
        ):
            self._register(t)
        # web_search only registers if a backing search API key is set.
        # Otherwise the model would see a tool it can't actually use and
        # smaller models tend to abort the task instead of falling back to
        # PubMed / arxiv / Europe PMC.
        import os
        if (
            self._cfg.secrets.TAVILY_API_KEY or os.environ.get("TAVILY_API_KEY")
            or self._cfg.secrets.BRAVE_API_KEY or os.environ.get("BRAVE_API_KEY")
        ):
            self._register(WebSearchTool(self._cfg))
        # Science-skills
        for meta in discover_skills(self._cfg):
            self._register(ScienceSkillTool(self._cfg, meta))
        return self

    def _register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            # later registrations win; log a warning at use site if needed
            pass
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools_for(self, agent: str) -> list[Tool]:
        allow = AGENT_TOOLS.get(agent, set())
        out: list[Tool] = []
        for t in self._tools.values():
            if t.name in allow:
                out.append(t)
            else:
                for pattern in allow:
                    if pattern.endswith("*") and t.name.startswith(pattern[:-1]):
                        out.append(t)
                        break
        return out

    def anthropic_tools_for(self, agent: str) -> list[dict[str, Any]]:
        return [to_anthropic_tool(t) for t in self.tools_for(agent)]

    async def call(
        self, name: str, args: dict[str, Any], ctx: ToolCtx
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(is_error=True, error_message=f"unknown tool: {name}")
        result = await tool.call(args, ctx)
        if not result.is_error:
            _save_retrieved_literature_artifact(self._cfg, name, args, ctx, result)
        return result

    def summary(self) -> list[dict[str, Any]]:
        """Used by `co-scientist tools list` and the UI."""
        return [
            {"name": t.name, "description": t.description[:200]}
            for t in sorted(self._tools.values(), key=lambda x: x.name)
        ]


def _save_retrieved_literature_artifact(
    cfg: Config,
    tool_name: str,
    args: dict[str, Any],
    ctx: ToolCtx,
    result: ToolResult,
) -> None:
    source = result.metadata.get("retrieval_source")
    if not source or ctx.session_id is None:
        return
    workspace = ScientistWorkspace(cfg, ctx.session_id)
    workspace.ensure()
    run_id = ctx.run_id or artifact_id()
    path = workspace.root / "retrieved_literature" / f"{tool_name}_{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tool": tool_name,
        "args": args,
        "content": result.content,
        "metadata": result.metadata,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    query = args.get("query") or (
        result.content.get("query") if isinstance(result.content, dict) else None
    )
    workspace.add_artifact(
        kind="retrieved_literature",
        path=path,
        title=f"{source}: {query or run_id}",
        provenance={"tool": tool_name, "run_id": run_id},
        metadata={
            "source": source,
            "query": query,
            "cache_hit": result.metadata.get("cache_hit"),
            "cache_key_args": args,
            "citation_metadata": _citation_metadata(result.content),
        },
    )


def _citation_metadata(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, dict):
        return []
    records = content.get("results")
    if not isinstance(records, list):
        return []
    citations: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        item = {
            key: record.get(key)
            for key in ("title", "url", "doi", "year", "pmid", "nct_id", "id")
            if record.get(key)
        }
        if item:
            citations.append(item)
    return citations
