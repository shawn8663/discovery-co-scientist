"""Supervisor follow-ups should refresh proximity before expensive reflection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from co_scientist import ids
from co_scientist.agents.supervisor import Supervisor
from co_scientist.models import Hypothesis, ResearchPlan, Session, Task, TaskResult
from co_scientist.storage.repos import hypotheses as hyp_repo
from co_scientist.storage.repos import sessions as sess_repo


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_session(conn) -> Session:
    session = Session(
        id="ses_proximity_schedule",
        created_at=_now(),
        updated_at=_now(),
        status="running",
        research_goal="Find a better assay design.",
        research_plan=ResearchPlan(objective="Find a better assay design."),
        config_snapshot={},
        budget_tokens=1000,
        budget_usd=1.0,
    )
    await sess_repo.insert(conn, session)
    return session


async def _insert_hypothesis(
    conn,
    session_id: str,
    index: int,
    *,
    state: str = "draft",
    matches_played: int = 0,
) -> str:
    hid = ids.hypothesis_id(session_id, "generation/literature", f"hypothesis {index}")
    await hyp_repo.insert(
        conn,
        Hypothesis(
            id=hid,
            session_id=session_id,
            created_at=_now() + timedelta(seconds=index),
            created_by="generation",
            strategy="literature",
            title=f"Hypothesis {index}",
            summary=f"Summary {index}",
            full_text=f"Full text {index}",
            artifact_path=f"artifacts/{session_id}/hypotheses/{hid}.json",
            state=state,  # type: ignore[arg-type]
            matches_played=matches_played,
        ),
    )
    return hid


@pytest.mark.asyncio
async def test_hypothesis_created_schedules_proximity_before_reflection(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    for i in range(2):
        await _insert_hypothesis(conn, session.id, i)
    new_id = await _insert_hypothesis(conn, session.id, 2)

    await Supervisor(tmp_cfg)._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="generation",
            action="CreateInitialHypotheses",
        ),
        TaskResult(kind="hypothesis_created", hypothesis_ids=[new_id]),
    )

    async with conn.execute(
        "SELECT agent, action, target_id, payload, priority, idempotency_key "
        "FROM tasks WHERE session_id=? ORDER BY priority ASC, created_at ASC",
        (session.id,),
    ) as cur:
        rows = await cur.fetchall()

    assert [row["agent"] for row in rows] == ["proximity", "reflection"]
    proximity = rows[0]
    reflection = rows[1]
    assert proximity["action"] == "UpdateProximityGraph"
    assert proximity["target_id"] is None
    assert json.loads(proximity["payload"]) == {
        "rebuild": True,
        "reason": "pre_reflection_duplicate_suppression",
    }
    assert proximity["priority"] < reflection["priority"]
    assert proximity["idempotency_key"] == f"{session.id}::proximity::pre_reflection::3"
    assert reflection["action"] == "ReviewHypothesis"
    assert reflection["target_id"] == new_id
    assert json.loads(reflection["payload"]) == {"kind": "screen"}
    assert reflection["idempotency_key"] == f"{new_id}::review::screen"


@pytest.mark.asyncio
async def test_promising_screen_review_schedules_full_reflection(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    hypothesis_id = await _insert_hypothesis(conn, session.id, 0)

    await Supervisor(tmp_cfg)._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="reflection",
            action="ReviewHypothesis",
            target_id=hypothesis_id,
            payload={"kind": "screen"},
        ),
        TaskResult(
            kind="review_completed",
            hypothesis_ids=[hypothesis_id],
            extra={"kind": "screen", "promising": True},
        ),
    )

    async with conn.execute(
        "SELECT agent, action, target_id, payload, idempotency_key "
        "FROM tasks WHERE session_id=?",
        (session.id,),
    ) as cur:
        row = await cur.fetchone()

    assert row["agent"] == "reflection"
    assert row["action"] == "ReviewHypothesis"
    assert row["target_id"] == hypothesis_id
    assert json.loads(row["payload"]) == {"kind": "full"}
    assert row["idempotency_key"] == f"{hypothesis_id}::review::full"


@pytest.mark.asyncio
async def test_low_promise_screen_review_skips_full_reflection(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    hypothesis_id = await _insert_hypothesis(conn, session.id, 0)

    await Supervisor(tmp_cfg)._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="reflection",
            action="ReviewHypothesis",
            target_id=hypothesis_id,
            payload={"kind": "screen"},
        ),
        TaskResult(
            kind="review_completed",
            hypothesis_ids=[hypothesis_id],
            extra={"kind": "screen", "promising": False},
        ),
    )

    async with conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE session_id=?",
        (session.id,),
    ) as cur:
        row = await cur.fetchone()

    assert row["n"] == 0


@pytest.mark.asyncio
async def test_full_review_schedules_ranking_add_to_tournament(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    hypothesis_id = await _insert_hypothesis(conn, session.id, 0)

    await Supervisor(tmp_cfg)._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="reflection",
            action="ReviewHypothesis",
            target_id=hypothesis_id,
            payload={"kind": "full"},
        ),
        TaskResult(
            kind="review_completed",
            hypothesis_ids=[hypothesis_id],
            extra={"kind": "full"},
        ),
    )

    async with conn.execute(
        "SELECT agent, action, target_id, payload, idempotency_key FROM tasks WHERE session_id=?",
        (session.id,),
    ) as cur:
        row = await cur.fetchone()

    assert row["agent"] == "ranking"
    assert row["action"] == "AddToTournament"
    assert row["target_id"] == hypothesis_id
    assert json.loads(row["payload"]) == {}
    assert row["idempotency_key"] == f"{hypothesis_id}::ranking::add"


@pytest.mark.asyncio
async def test_add_to_tournament_schedules_focused_ranking_batch(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    hypothesis_id = await _insert_hypothesis(conn, session.id, 0)

    await Supervisor(tmp_cfg)._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="ranking",
            action="AddToTournament",
            target_id=hypothesis_id,
        ),
        TaskResult(kind="added_to_tournament", hypothesis_ids=[hypothesis_id]),
    )

    async with conn.execute(
        "SELECT agent, action, payload, idempotency_key FROM tasks WHERE session_id=?",
        (session.id,),
    ) as cur:
        row = await cur.fetchone()

    assert row["agent"] == "ranking"
    assert row["action"] == "RunTournamentBatch"
    assert json.loads(row["payload"]) == {"focus": hypothesis_id}
    assert row["idempotency_key"] == f"{hypothesis_id}::ranking::focus_batch"


@pytest.mark.asyncio
async def test_idle_flow_schedules_ranking_evolution_and_metareview(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    hyp_ids = [
        await _insert_hypothesis(
            conn,
            session.id,
            i,
            state="in_tournament",
            matches_played=3,
        )
        for i in range(20)
    ]
    now = _now().isoformat()
    for i in range(50):
        await conn.execute(
            """INSERT INTO tournament_matches(
                   id, session_id, created_at, hyp_a, hyp_b, mode, winner,
                   elo_a_before, elo_b_before, elo_a_after, elo_b_after)
               VALUES (?, ?, ?, ?, ?, 'pairwise', 'a', 1200, 1200, 1216, 1184)""",
            (f"mat_s8_{i}", session.id, now, hyp_ids[0], hyp_ids[1]),
        )
    await conn.commit()

    n = await Supervisor(tmp_cfg)._decide_next_steps(conn, session)

    assert n == 3
    async with conn.execute(
        "SELECT agent, action, payload FROM tasks WHERE session_id=? ORDER BY priority ASC",
        (session.id,),
    ) as cur:
        rows = await cur.fetchall()

    assert [(row["agent"], row["action"]) for row in rows] == [
        ("evolution", "EvolveTopHypotheses"),
        ("ranking", "RunTournamentBatch"),
        ("metareview", "GenerateSystemFeedback"),
    ]
    assert json.loads(rows[0]["payload"]) == {
        "top_k": 5,
        "strategies": ["combine", "simplify", "out_of_box"],
    }


@pytest.mark.asyncio
async def test_hypothesis_created_without_new_ids_skips_proximity(tmp_cfg, conn) -> None:
    session = await _make_session(conn)
    for i in range(3):
        await _insert_hypothesis(conn, session.id, i)

    await Supervisor(tmp_cfg)._apply_follow_ups(
        conn,
        session,
        Task(
            id=ids.task_id(),
            session_id=session.id,
            created_at=_now(),
            agent="generation",
            action="CreateInitialHypotheses",
        ),
        TaskResult(kind="hypothesis_created", hypothesis_ids=[]),
    )

    async with conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE session_id=?",
        (session.id,),
    ) as cur:
        row = await cur.fetchone()

    assert row["n"] == 0
