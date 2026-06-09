"""Tests for the safety classifier's action mapping (no API calls)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from co_scientist.config import Config
from co_scientist.safety.classifier import ClassifierResult, SafetyClassifier


def test_benign_is_allowed() -> None:
    r = ClassifierResult(categories=["none"], confidence=1.0, rationale="ok")
    assert r.is_benign
    assert r.action(Config()) == "allow"


def test_block_categories_block() -> None:
    cfg = Config()
    r = ClassifierResult(categories=["cbrn"], confidence=0.99, rationale="x")
    assert not r.is_benign
    assert r.action(cfg) == "block"


def test_warn_high_confidence_quarantines() -> None:
    cfg = Config()
    r = ClassifierResult(categories=["dual_use_bio"], confidence=0.8, rationale="x")
    assert r.action(cfg) == "quarantine"


def test_warn_low_confidence_warns() -> None:
    cfg = Config()
    r = ClassifierResult(categories=["dual_use_bio"], confidence=0.4, rationale="x")
    assert r.action(cfg) == "warn"


def test_unflagged_other_category_allows() -> None:
    cfg = Config()
    r = ClassifierResult(categories=["unknown_label"], confidence=0.5, rationale="x")
    assert r.action(cfg) == "allow"


def test_safety_unavailable_uses_configured_failure_action() -> None:
    cfg = Config()
    cfg.safety.classifier_failure_action = "quarantine"
    r = ClassifierResult(categories=["safety_unavailable"], confidence=1.0, rationale="x")
    assert r.action(cfg) == "quarantine"


@pytest.mark.asyncio
async def test_missing_key_fails_open_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.secrets.ANTHROPIC_API_KEY = ""
    r = await SafetyClassifier(cfg).classify("benign goal")
    assert r.categories == ["none"]
    assert r.action(cfg) == "allow"
    assert "fail-open" in r.rationale


@pytest.mark.asyncio
async def test_missing_key_can_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.secrets.ANTHROPIC_API_KEY = ""
    cfg.safety.classifier_fail_open_in_dev = False
    r = await SafetyClassifier(cfg).classify("benign goal")
    assert r.categories == ["safety_unavailable"]
    assert r.action(cfg) == "block"


@pytest.mark.asyncio
async def test_openai_provider_uses_configured_classifier_model(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.llm.provider = "openai"
    cfg.models.classifier = "gpt-5"
    cfg.secrets.OPENAI_API_KEY = "sk-openai-fake"

    raw = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="record_safety_assessment",
                                arguments=(
                                    '{"categories":["dual_use_bio"],'
                                    '"confidence":0.7,"rationale":"possible misuse"}'
                                ),
                            )
                        )
                    ]
                )
            )
        ]
    )
    create = AsyncMock(return_value=raw)
    sdk = MagicMock()
    sdk.chat.completions.create = create

    with patch("openai.AsyncOpenAI", return_value=sdk) as mock_openai:
        result = await SafetyClassifier(cfg).classify("biosecurity-adjacent goal")

    mock_openai.assert_called_once_with(api_key="sk-openai-fake")
    create.assert_awaited_once()
    request = create.await_args.kwargs
    assert request["model"] == "gpt-5"
    assert request["tool_choice"]["function"]["name"] == "record_safety_assessment"
    assert result.categories == ["dual_use_bio"]
    assert result.action(cfg) == "quarantine"


@pytest.mark.asyncio
async def test_non_anthropic_provider_falls_back_to_primary_model_for_default_classifier(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.llm.provider = "openai"
    cfg.models.parse_goal = "gpt-5"
    cfg.secrets.OPENAI_API_KEY = "sk-openai-fake"

    raw = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="record_safety_assessment",
                                arguments='{"categories":["none"],"confidence":1.0,"rationale":"ok"}',
                            )
                        )
                    ]
                )
            )
        ]
    )
    create = AsyncMock(return_value=raw)
    sdk = MagicMock()
    sdk.chat.completions.create = create

    with patch("openai.AsyncOpenAI", return_value=sdk):
        result = await SafetyClassifier(cfg).classify("benign goal")

    assert create.await_args.kwargs["model"] == "gpt-5"
    assert result.categories == ["none"]


@pytest.mark.asyncio
async def test_openai_provider_missing_key_reports_openai_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config()
    cfg.llm.provider = "openai"
    cfg.secrets.OPENAI_API_KEY = ""
    cfg.safety.classifier_fail_open_in_dev = False

    result = await SafetyClassifier(cfg).classify("benign goal")

    assert result.categories == ["safety_unavailable"]
    assert "OPENAI_API_KEY" in result.rationale
