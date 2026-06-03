"""Tests for the ranking verdict parser and mode-selection logic."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from co_scientist.agents.ranking import RankingAgent, _parse_better_idea
from co_scientist.config import Config
from co_scientist.llm.anthropic_client import AnthropicResponse
from co_scientist.models import Hypothesis, ResearchPlan, Review, ReviewScores, Session, Task
from co_scientist.storage.repos import events as events_repo
from co_scientist.storage.repos import hypotheses as hyp_repo
from co_scientist.storage.repos import reviews as rev_repo
from co_scientist.storage.repos import sessions as sess_repo

# ----------------------------- verdict parser ----------------------------- #

def test_parse_better_idea_basic() -> None:
    assert _parse_better_idea("blah\nbetter idea: 1") == 1
    assert _parse_better_idea("blah\nbetter idea: 2") == 2


def test_parse_better_idea_trailing_marker_wins() -> None:
    text = "An earlier mention: better idea: 1\n\nFinal verdict.\nbetter idea: 2"
    assert _parse_better_idea(text) == 2


def test_parse_better_idea_handles_case_and_punctuation() -> None:
    assert _parse_better_idea("...\nBetter Idea: 2.") == 2
    assert _parse_better_idea("...\n**better idea**: 1") == 1


def test_parse_better_idea_returns_none_when_missing() -> None:
    assert _parse_better_idea("no verdict here") is None
    assert _parse_better_idea("") is None


def test_parse_better_idea_handles_qualifier_words() -> None:
    """Regression: the prior 'in tail.split()[0:1]' check rejected these."""
    assert _parse_better_idea("better idea: option 1") == 1
    assert _parse_better_idea("better idea: hypothesis 2") == 2
    assert _parse_better_idea("better idea: hyp 1") == 1


def test_parse_better_idea_word_boundary_excludes_12() -> None:
    """'better idea: 12 because...' must NOT be read as '1'."""
    # `12` should not match `[12]\b`.
    assert _parse_better_idea("better idea: 12 because of context") is None


# ----------------------------- mode selection ----------------------------- #

def _h(
    *,
    elo: float,
    matches: int,
    hid: str = "hyp_x",
    cluster: str | None = None,
) -> Hypothesis:
    return Hypothesis(
        id=hid, session_id="ses", created_at=datetime.now(UTC),
        created_by="generation", strategy="literature",
        title="t", summary="s", full_text="f",
        artifact_path=f"artifacts/ses/hypotheses/{hid}.json",
        elo=elo, matches_played=matches, state="in_tournament",
        dedup_cluster=cluster,
    )


def _agent() -> RankingAgent:
    deps = MagicMock()
    deps.cfg = Config()
    return RankingAgent(deps)


def test_mode_debate_when_either_player_has_few_matches() -> None:
    a = _h(hid="a", elo=1500, matches=0)
    b = _h(hid="b", elo=1500, matches=10)
    assert _agent()._select_mode(a, b) == "debate"


def test_mode_debate_when_elo_gap_is_small() -> None:
    a = _h(hid="a", elo=1500, matches=5)
    b = _h(hid="b", elo=1520, matches=5)
    assert _agent()._select_mode(a, b) == "debate"


def test_mode_pairwise_when_warm_and_large_gap() -> None:
    a = _h(hid="a", elo=1500, matches=10)
    b = _h(hid="b", elo=1300, matches=10)
    assert _agent()._select_mode(a, b) == "pairwise"


# ----------------------------- nearest-Elo helper ----------------------------- #

def test_nearest_elo_picks_closest() -> None:
    target = _h(hid="t", elo=1300, matches=0)
    pool = [
        _h(hid="a", elo=1000, matches=5),
        _h(hid="b", elo=1310, matches=5),    # closest
        _h(hid="c", elo=1500, matches=5),
    ]
    nearest = _agent()._nearest_elo(target, pool)
    assert nearest is not None and nearest.id == "b"


def test_nearest_elo_empty_pool() -> None:
    target = _h(hid="t", elo=1300, matches=0)
    assert _agent()._nearest_elo(target, []) is None


def test_nearest_elo_prefers_cross_cluster_when_available() -> None:
    target = _h(hid="t", elo=1300, matches=0, cluster="cluster-a")
    pool = [
        _h(hid="near_duplicate", elo=1301, matches=5, cluster="cluster-a"),
        _h(hid="cross_cluster", elo=1450, matches=5, cluster="cluster-b"),
    ]

    nearest = _agent()._nearest_elo(target, pool)

    assert nearest is not None and nearest.id == "cross_cluster"


def test_nearest_elo_falls_back_to_same_cluster_when_needed() -> None:
    target = _h(hid="t", elo=1300, matches=0, cluster="cluster-a")
    pool = [
        _h(hid="only_duplicate", elo=1301, matches=5, cluster="cluster-a"),
    ]

    nearest = _agent()._nearest_elo(target, pool)

    assert nearest is not None and nearest.id == "only_duplicate"


def test_sample_close_elo_prefers_cross_cluster_pairs() -> None:
    pool = [
        _h(hid="a", elo=1200, matches=5, cluster="cluster-a"),
        _h(hid="b", elo=1201, matches=5, cluster="cluster-a"),
        _h(hid="c", elo=1210, matches=5, cluster="cluster-b"),
    ]

    pair = _agent()._sample_close_elo(store=None, pool=pool)

    assert pair is not None
    assert pair[0].dedup_cluster != pair[1].dedup_cluster


@pytest.mark.asyncio
async def test_select_pair_with_focus_prefers_cross_cluster_opponent() -> None:
    agent = _agent()
    agent._load_store = AsyncMock(return_value=None)
    candidates = [
        _h(hid="focus", elo=1300, matches=0, cluster="cluster-a"),
        _h(hid="near_duplicate", elo=1301, matches=5, cluster="cluster-a"),
        _h(hid="cross_cluster", elo=1450, matches=5, cluster="cluster-b"),
    ]

    pair = await agent._select_pair("ses", candidates, focus_id="focus")

    assert pair is not None
    assert {pair[0].id, pair[1].id} == {"focus", "cross_cluster"}


@pytest.mark.asyncio
async def test_ranking_emits_match_trace_event(tmp_cfg, conn) -> None:
    now = datetime.now(UTC)
    session = Session(
        id="ses_ranking_trace",
        created_at=now,
        updated_at=now,
        status="running",
        research_goal="Compare hypotheses.",
        research_plan=ResearchPlan(objective="Compare hypotheses."),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    for hid in ("hyp_a", "hyp_b"):
        await hyp_repo.insert(conn, Hypothesis(
            id=hid,
            session_id=session.id,
            created_at=now,
            created_by="generation",
            strategy="literature",
            title=hid,
            summary="s",
            full_text="f",
            artifact_path=f"artifacts/{session.id}/hypotheses/{hid}.json",
            state="reviewed",
        ))
        await hyp_repo.init_tournament(conn, hid, initial_elo=1200)
        await rev_repo.insert(conn, Review(
            id=f"rev_{hid}",
            hypothesis_id=hid,
            session_id=session.id,
            created_at=now,
            kind="full",
            verdict="missing_piece",
            scores=ReviewScores(novelty=0.8, correctness=0.7, testability=0.6),
            body=(
                "# Review\n\n"
                "**Verdict.** missing_piece\n\n"
                "**Scores.** novelty 0.80 · correctness 0.70 · testability 0.60\n\n"
                "## Evidence\n"
                "- Relevant claim — https://example.test/paper\n  > quote\n\n"
                "## Notes\n" + " ".join(f"long-note-{i}" for i in range(500))
            ),
            artifact_path=f"artifacts/{session.id}/reviews/rev_{hid}.json",
        ))

    raw = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Reasoning.\nbetter idea: 1")],
    )
    response = AnthropicResponse(
        raw=raw,
        transcript_id="trn_rank",
        cost_usd=0.03,
        input_tokens=900,
        output_tokens=150,
        cache_read=200,
        cache_write=50,
    )
    deps = MagicMock()
    deps.cfg = tmp_cfg
    deps.db = conn
    deps.llm.call = AsyncMock(return_value=response)
    agent = RankingAgent(deps)

    result = await agent.execute(Task(
        id="task_rank_trace",
        session_id=session.id,
        created_at=now,
        agent="ranking",
        action="RunTournamentBatch",
        payload={"focus": "hyp_a"},
    ))

    assert result.kind == "tournament_match_complete"
    spec = deps.llm.call.call_args.args[0]
    assert spec.stop_sequences is None
    recent = await events_repo.recent(conn, session.id, limit=10)
    event = next(e for e in recent if e["event"] == "ranking_match_trace")
    assert event["agent"] == "ranking"
    assert event["payload"]["match_id"] == result.match_ids[0]
    assert event["payload"]["model"] == tmp_cfg.models.ranking_debate
    assert event["payload"]["transcript_id"] == "trn_rank"
    assert event["payload"]["input_tokens"] == 900
    assert event["payload"]["cache_read"] == 200
    assert event["payload"]["cost_usd"] == pytest.approx(0.03)
    assert event["payload"]["duration_ms"] >= 0
    assert event["payload"]["review_chars_original"] > event["payload"]["review_chars_sent"]
    assert event["payload"]["review_chars_saved"] > 0
