"""Tests for the obs/metrics aggregations."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from co_scientist.models import Hypothesis, ResearchPlan, Session, Transcript
from co_scientist.obs.metrics import session_metrics, to_dict
from co_scientist.storage.repos import events as events_repo
from co_scientist.storage.repos import hypotheses as hyp_repo
from co_scientist.storage.repos import sessions as sess_repo
from co_scientist.storage.repos import transcripts as tx_repo


async def _seed(conn, session_id: str = "ses_m") -> None:
    now = datetime.now(UTC)
    await sess_repo.insert(conn, Session(
        id=session_id, created_at=now, updated_at=now, status="running",
        research_goal="g", research_plan=ResearchPlan(objective="o"),
        config_snapshot={}, budget_tokens=1_000_000, budget_usd=10.0,
    ))
    await hyp_repo.insert(conn, Hypothesis(
        id="hyp_x", session_id=session_id, created_at=now,
        created_by="generation", strategy="literature",
        title="t", summary="s", full_text="f",
        artifact_path=f"artifacts/{session_id}/hypotheses/hyp_x.json",
        state="in_tournament", elo=1234.0, matches_played=3,
    ))
    await tx_repo.insert(conn, Transcript(
        id="trn_1", session_id=session_id, task_id=None,
        agent="generation", action="x", model="claude-opus-4-7",
        input_tokens=1000, output_tokens=200, cache_read=500, cache_write=0,
        cost_usd=0.12, started_at=now, finished_at=now,
        artifact_path=f"artifacts/{session_id}/transcripts/generation/trn_1.json",
    ))


@pytest.mark.asyncio
async def test_session_metrics_aggregates(conn) -> None:
    await _seed(conn)
    m = await session_metrics(conn, "ses_m")
    assert m.n_calls == 1
    assert m.input_tokens == 1000
    assert m.n_hypotheses == 1
    assert m.n_in_tournament == 1
    assert m.cost_usd == pytest.approx(0.12)
    # cache_hit_ratio = 500 / (500 + 0 + 1000) = 1/3
    assert m.cache_hit_ratio == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_session_metrics_to_dict_roundtrips(conn) -> None:
    await _seed(conn, session_id="ses_m2")
    m = await session_metrics(conn, "ses_m2")
    d = to_dict(m)
    for key in (
        "n_calls", "input_tokens", "n_hypotheses",
        "n_in_tournament", "cost_usd", "cache_hit_ratio",
    ):
        assert key in d


@pytest.mark.asyncio
async def test_session_metrics_counts_duplicate_rates(conn) -> None:
    await _seed(conn, session_id="ses_dups")
    now = datetime.now(UTC)
    await hyp_repo.insert(conn, Hypothesis(
        id="hyp_retired", session_id="ses_dups", created_at=now,
        created_by="generation", strategy="literature",
        title="retired", summary="s", full_text="f",
        artifact_path="artifacts/ses_dups/hypotheses/hyp_retired.json",
        state="retired", dedup_cluster="c0001",
    ))
    await events_repo.emit(
        conn,
        session_id="ses_dups",
        task_id=None,
        agent="generation",
        event="hypothesis_duplicate_suppressed",
        payload={"reason": "semantic", "proposed_hypothesis_id": "hyp_sem"},
    )
    await events_repo.emit(
        conn,
        session_id="ses_dups",
        task_id=None,
        agent="generation",
        event="hypothesis_duplicate_suppressed",
        payload={"reason": "deterministic", "proposed_hypothesis_id": "hyp_det"},
    )
    await events_repo.emit(
        conn,
        session_id="ses_dups",
        task_id=None,
        agent="reflection",
        event="hypothesis_duplicate_suppressed",
        payload={"reason": "clustered", "proposed_hypothesis_id": "hyp_retired"},
    )

    m = await session_metrics(conn, "ses_dups")

    assert m.n_hypothesis_attempts == 4
    assert m.n_duplicate_hypotheses == 3
    assert m.n_deterministic_duplicates == 1
    assert m.n_semantic_duplicates == 1
    assert m.n_clustered_duplicates_retired == 1
    assert m.n_duplicates_reaching_tournament == 0
    assert m.duplicate_rate == pytest.approx(0.75)
    assert m.tournament_duplicate_rate == pytest.approx(0.0)
    d = to_dict(m)
    assert d["n_duplicate_hypotheses"] == 3
    assert d["duplicate_rate"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_session_metrics_counts_retrieval_cache_and_latency(conn) -> None:
    await _seed(conn, session_id="ses_retrieval_metrics")
    await events_repo.emit(
        conn,
        session_id="ses_retrieval_metrics",
        task_id=None,
        agent="generation",
        event="tool_call",
        payload={
            "name": "openalex_search",
            "duration_ms": 20,
            "metadata": {"retrieval_source": "openalex_search", "cache_hit": True},
        },
    )
    await events_repo.emit(
        conn,
        session_id="ses_retrieval_metrics",
        task_id=None,
        agent="generation",
        event="tool_call",
        payload={
            "name": "clinical_trials_search",
            "duration_ms": 40,
            "metadata": {"retrieval_source": "clinical_trials_search", "cache_hit": False},
        },
    )

    m = await session_metrics(conn, "ses_retrieval_metrics")

    assert m.retrieval_tool_calls == 2
    assert m.retrieval_cache_hits == 1
    assert m.retrieval_cache_misses == 1
    assert m.retrieval_cache_hit_ratio == pytest.approx(0.5)
    assert m.retrieval_latency_ms_total == 60
    assert m.retrieval_latency_ms_avg == pytest.approx(30)
    assert m.retrieval_sources["openalex_search"]["cache_hits"] == 1
    assert m.retrieval_sources["clinical_trials_search"]["cache_misses"] == 1
    assert to_dict(m)["retrieval_cache_hit_ratio"] == pytest.approx(0.5)
