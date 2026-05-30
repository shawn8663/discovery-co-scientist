"""BaseAgent — shared run-loop plumbing for all six specialized agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite

from ..config import Config
from ..llm.provider import LLMProvider
from ..models import Task, TaskResult
from ..safety.quoting import SAFETY_PREAMBLE
from ..storage.repos import events as events_repo
from ..tools.registry import ToolRegistry


@dataclass
class AgentDeps:
    """Bundle of resources every agent needs."""

    cfg: Config
    db: aiosqlite.Connection
    llm: LLMProvider
    tools: ToolRegistry


class BaseAgent:
    name: str = "base"

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps

    # Subclasses override
    async def execute(self, task: Task) -> TaskResult:  # pragma: no cover
        raise NotImplementedError

    # ----------------------------- helpers ----------------------------- #

    def _system_prompt_header(self) -> str:
        """Common safety preamble prepended to every agent's system prompt."""
        return (
            f"You are the {self.name} agent in a multi-agent scientific research system. "
            f"Operate carefully and cite your sources. {SAFETY_PREAMBLE}"
        )

    @staticmethod
    def _final_tool_use(response, tool_name: str) -> dict[str, Any] | None:
        """Find the most recent tool_use block with the given name in a response.

        Returns the .input dict, or None if not present.
        """
        for block in reversed(response.raw.content or []):
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == tool_name:
                inp = getattr(block, "input", None)
                return dict(inp) if isinstance(inp, dict) else None
        return None

    @staticmethod
    def _final_text(response) -> str:
        parts = []
        for block in response.raw.content or []:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", ""))
        return "\n".join(parts).strip()

    async def _emit_tool_call_events(
        self,
        *,
        session_id: str,
        task_id: str | None,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        for call in tool_calls:
            await events_repo.emit(
                self.deps.db,
                session_id=session_id,
                task_id=task_id,
                agent=self.name,
                event="tool_call",
                payload=call,
            )
