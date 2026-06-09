"""Abuse / dual-use classifier.

A provider-backed classifier with a structured-output tool. Placement:
- goal-parse time (mandatory): block / warn / allow
- hypothesis-save time (mandatory): ok / quarantine / block
- final-report time (optional): ok / redact_quarantined / block_publish

Defensive use only. In local development the classifier can fail open so agents
stay functional without a key. Production-style configs should set
`safety.classifier_fail_open_in_dev = false`, which turns missing keys or model
errors into a configurable safety action.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from anthropic import AsyncAnthropic

from ..config import Config, provider_key_env
from ..llm.provider import OPENAI_COMPAT_PRESETS
from ..logging import get_logger

log = get_logger("safety.classifier")


Action = Literal["allow", "warn", "block", "quarantine"]


CLASSIFY_TOOL: dict[str, Any] = {
    "name": "record_safety_assessment",
    "description": "Record a structured safety assessment of input text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "none",
                    "dual_use_bio",
                    "cbrn",
                    "weapons",
                    "illicit_synthesis",
                    "csam",
                    "safety_unavailable",
                ],
                },
                "description": "All categories that apply. Use ['none'] if benign.",
            },
            "confidence": {
                "type": "number", "minimum": 0, "maximum": 1,
                "description": "0..1 confidence in the worst-case categorization.",
            },
            "rationale": {"type": "string"},
        },
        "required": ["categories", "confidence", "rationale"],
    },
}


_SYSTEM_PROMPT = (
    "You are a content-safety classifier for an AI research-hypothesis system. "
    "You will be shown a passage of text (a research goal, a hypothesis, or a "
    "synthesized report). Classify the content into one or more categories and "
    "report your confidence.\n\n"
    "Categories:\n"
    " - none: benign scientific content.\n"
    " - dual_use_bio: research that could plausibly be misused for biological "
    "harm (gain-of-function, enhancement of pathogen transmissibility, etc.).\n"
    " - cbrn: chemical, biological, radiological, or nuclear weapons.\n"
    " - weapons: conventional weapons synthesis or improvement.\n"
    " - illicit_synthesis: drug or precursor synthesis routes for unlawful use.\n"
    " - csam: child sexual abuse material.\n\n"
    "Be precise. Benign biology research is NOT dual_use_bio; only flag when the "
    "stated outcome could plausibly increase harm capability. When in doubt, "
    "use 'none' with low confidence. Always call the record_safety_assessment tool."
)


class ClassifierResult:
    def __init__(self, categories: list[str], confidence: float, rationale: str) -> None:
        self.categories = categories or ["none"]
        self.confidence = confidence
        self.rationale = rationale

    @property
    def is_benign(self) -> bool:
        return self.categories == ["none"] or ("none" in self.categories and len(self.categories) == 1)

    def action(self, cfg: Config) -> Action:
        if "safety_unavailable" in self.categories:
            return cfg.safety.classifier_failure_action
        if self.is_benign:
            return "allow"
        block = set(cfg.safety.classifier_block_categories)
        warn = set(cfg.safety.classifier_warn_categories)
        flagged = set(self.categories) - {"none"}
        if flagged & block:
            return "block"
        if flagged & warn and self.confidence >= 0.6:
            return "quarantine"
        if flagged & warn:
            return "warn"
        return "allow"


class SafetyClassifier:
    """One classifier per Config, backed by the configured LLM provider."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._provider = (cfg.llm.provider or "anthropic").strip().lower()
        self._client: AsyncAnthropic | None = None
        self._openai_client: Any | None = None
        self._unavailable_reason = ""
        if self._provider == "anthropic":
            api_key = cfg.secrets.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY") or ""
            if api_key:
                self._client = AsyncAnthropic(api_key=api_key)
            else:
                self._unavailable_reason = "missing ANTHROPIC_API_KEY"
            return

        if self._provider in {"openai", "openai_compatible"} or self._provider in OPENAI_COMPAT_PRESETS:
            self._openai_client = self._build_openai_client()
            return

        self._unavailable_reason = f"unsupported provider {self._provider!r}"

    async def classify(self, text: str, *, label: str = "input") -> ClassifierResult:
        """Always returns a result.

        Local dev can fail open by config; stricter deployments can fail closed
        without crashing the session.
        """
        if not self._cfg.safety.enable_classifier:
            return ClassifierResult(
                categories=["none"],
                confidence=0.0,
                rationale="classifier disabled",
            )
        if self._client is None and self._openai_client is None:
            if not self._cfg.safety.classifier_fail_open_in_dev:
                return ClassifierResult(
                    categories=["safety_unavailable"],
                    confidence=1.0,
                    rationale=f"classifier unavailable: {self._unavailable_reason}",
                )
            return ClassifierResult(
                categories=["none"],
                confidence=0.0,
                rationale=f"classifier unavailable: {self._unavailable_reason}; fail-open dev mode",
            )
        text = text.strip()
        if not text:
            return ClassifierResult(categories=["none"], confidence=1.0,
                                    rationale="empty input")
        try:
            if self._client is not None:
                resp = await self._classify_anthropic(text, label)
            else:
                resp = await self._classify_openai(text, label)
        except Exception as e:
            log.warning("classifier_call_failed", err=str(e))
            if not self._cfg.safety.classifier_fail_open_in_dev:
                return ClassifierResult(
                    categories=["safety_unavailable"],
                    confidence=1.0,
                    rationale=f"classifier_error: {e!s}",
                )
            return ClassifierResult(categories=["none"], confidence=0.0,
                                    rationale=f"classifier_error: {e!s}")
        if isinstance(resp, dict):
            return ClassifierResult(
                categories=list(resp.get("categories", ["none"])),
                confidence=float(resp.get("confidence", 0.0)),
                rationale=str(resp.get("rationale", "")),
            )
        for b in resp.content:
            if getattr(b, "type", None) == "tool_use" and getattr(b, "name", "") == "record_safety_assessment":
                inp = getattr(b, "input", None)
                if isinstance(inp, dict):
                    return ClassifierResult(
                        categories=list(inp.get("categories", ["none"])),
                        confidence=float(inp.get("confidence", 0.0)),
                        rationale=str(inp.get("rationale", "")),
                    )
        return ClassifierResult(categories=["none"], confidence=0.0,
                                rationale="no tool_use block in response")

    def _build_openai_client(self) -> Any | None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            self._unavailable_reason = "openai SDK is not installed"
            return None

        preset = OPENAI_COMPAT_PRESETS.get(self._provider)
        preset_api_key_env = str(preset.get("api_key_env", "")) if preset else ""
        preset_base_url = str(preset.get("base_url", "")) if preset else ""
        api_key = self._cfg.secrets.OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY") or ""
        if not api_key and preset_api_key_env:
            api_key = (
                getattr(self._cfg.secrets, preset_api_key_env, "")
                or os.environ.get(preset_api_key_env)
                or ""
            )
        compat_mode = self._provider == "openai_compatible" or bool(preset)
        if not api_key and compat_mode:
            api_key = "compat-no-key"
        if not api_key:
            env_var = provider_key_env(self._cfg) or preset_api_key_env or "OPENAI_API_KEY"
            self._unavailable_reason = f"missing {env_var}"
            return None

        base_url = (
            getattr(self._cfg.llm.openai, "base_url", None)
            or os.environ.get("OPENAI_BASE_URL")
            or preset_base_url
            or None
        )
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return AsyncOpenAI(**kwargs)

    def _classifier_model(self) -> str:
        model = self._cfg.models.classifier
        if self._provider != "anthropic" and model.startswith("claude-"):
            for candidate in (self._cfg.models.parse_goal, self._cfg.models.generation):
                if candidate and not candidate.startswith("claude-"):
                    return candidate
        return model

    async def _classify_anthropic(self, text: str, label: str) -> Any:
        if self._client is None:  # pragma: no cover - guarded by classify
            raise RuntimeError("anthropic classifier client is unavailable")
        return await self._client.messages.create(
            model=self._classifier_model(),
            system=_SYSTEM_PROMPT,
            max_tokens=512,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "record_safety_assessment"},
            messages=[
                {"role": "user", "content": f"<TEXT label=\"{label}\">\n{text[:8000]}\n</TEXT>"},
            ],
        )

    async def _classify_openai(self, text: str, label: str) -> dict[str, Any]:
        if self._openai_client is None:  # pragma: no cover - guarded by classify
            raise RuntimeError("OpenAI-compatible classifier client is unavailable")
        raw = await self._openai_client.chat.completions.create(
            model=self._classifier_model(),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"<TEXT label=\"{label}\">\n{text[:8000]}\n</TEXT>"},
            ],
            max_tokens=512,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": CLASSIFY_TOOL["name"],
                        "description": CLASSIFY_TOOL["description"],
                        "parameters": CLASSIFY_TOOL["input_schema"],
                    },
                }
            ],
            tool_choice={
                "type": "function",
                "function": {"name": CLASSIFY_TOOL["name"]},
            },
        )
        choice = raw.choices[0] if getattr(raw, "choices", None) else None
        tool_calls = getattr(getattr(choice, "message", None), "tool_calls", None) or []
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            if getattr(fn, "name", "") != CLASSIFY_TOOL["name"]:
                continue
            args_raw = getattr(fn, "arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except (TypeError, json.JSONDecodeError):
                args = {}
            if isinstance(args, dict):
                return args
        return {"categories": ["none"], "confidence": 0.0, "rationale": "no tool call in response"}
