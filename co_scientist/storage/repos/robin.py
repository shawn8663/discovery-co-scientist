"""Repositories for Robin-style therapeutic discovery entities."""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from ...models import (
    AnalysisRun,
    AssayEvaluation,
    AssayProposal,
    ExperimentInsight,
    TherapeuticCandidate,
    TherapeuticCandidateEvaluation,
)


async def insert_assay(conn: aiosqlite.Connection, assay: AssayProposal) -> bool:
    cur = await conn.execute(
        """INSERT OR IGNORE INTO assay_proposals(
               id, session_id, created_at, round_index, strategy_name, reasoning,
               artifact_path, rank_score, state)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            assay.id,
            assay.session_id,
            assay.created_at.isoformat(),
            assay.round_index,
            assay.strategy_name,
            assay.reasoning,
            assay.artifact_path,
            assay.rank_score,
            assay.state,
        ),
    )
    await conn.commit()
    return cur.rowcount > 0


async def list_assays(conn: aiosqlite.Connection, session_id: str) -> list[AssayProposal]:
    async with conn.execute(
        """SELECT * FROM assay_proposals
             WHERE session_id=?
             ORDER BY round_index ASC, rank_score DESC NULLS LAST, created_at ASC""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_assay(r) for r in rows]


async def fetch_assay(conn: aiosqlite.Connection, assay_id: str) -> AssayProposal | None:
    async with conn.execute("SELECT * FROM assay_proposals WHERE id=?", (assay_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_assay(row) if row else None


async def set_assay_rank_score(
    conn: aiosqlite.Connection, assay_id: str, rank_score: float, *, state: str = "ranked"
) -> None:
    await conn.execute(
        "UPDATE assay_proposals SET rank_score=?, state=? WHERE id=?",
        (rank_score, state, assay_id),
    )
    await conn.commit()


async def set_assay_state(conn: aiosqlite.Connection, assay_id: str, state: str) -> None:
    await conn.execute(
        "UPDATE assay_proposals SET state=? WHERE id=?",
        (state, assay_id),
    )
    await conn.commit()


async def insert_assay_evaluation(
    conn: aiosqlite.Connection, evaluation: AssayEvaluation
) -> bool:
    cur = await conn.execute(
        """INSERT OR IGNORE INTO assay_evaluations(
               id, assay_id, session_id, created_at, overview, biomedical_evidence,
               previous_use, overall_evaluation, artifact_path)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            evaluation.id,
            evaluation.assay_id,
            evaluation.session_id,
            evaluation.created_at.isoformat(),
            evaluation.overview,
            evaluation.biomedical_evidence,
            evaluation.previous_use,
            evaluation.overall_evaluation,
            evaluation.artifact_path,
        ),
    )
    await conn.commit()
    return cur.rowcount > 0


async def list_assay_evaluations(
    conn: aiosqlite.Connection, session_id: str
) -> list[AssayEvaluation]:
    async with conn.execute(
        "SELECT * FROM assay_evaluations WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_assay_evaluation(r) for r in rows]


async def insert_candidate(conn: aiosqlite.Connection, candidate: TherapeuticCandidate) -> bool:
    cur = await conn.execute(
        """INSERT OR IGNORE INTO therapeutic_candidates(
               id, session_id, assay_id, created_at, round_index, candidate, hypothesis,
               reasoning, artifact_path, rank_score, state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            candidate.id,
            candidate.session_id,
            candidate.assay_id,
            candidate.created_at.isoformat(),
            candidate.round_index,
            candidate.candidate,
            candidate.hypothesis,
            candidate.reasoning,
            candidate.artifact_path,
            candidate.rank_score,
            candidate.state,
        ),
    )
    await conn.commit()
    return cur.rowcount > 0


async def list_candidates(
    conn: aiosqlite.Connection, session_id: str
) -> list[TherapeuticCandidate]:
    async with conn.execute(
        """SELECT * FROM therapeutic_candidates
             WHERE session_id=?
             ORDER BY round_index ASC, rank_score DESC NULLS LAST, created_at ASC""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_candidate(r) for r in rows]


async def fetch_candidate(
    conn: aiosqlite.Connection, candidate_id: str
) -> TherapeuticCandidate | None:
    async with conn.execute(
        "SELECT * FROM therapeutic_candidates WHERE id=?", (candidate_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_candidate(row) if row else None


async def set_candidate_rank_score(
    conn: aiosqlite.Connection,
    candidate_id: str,
    rank_score: float,
    *,
    state: str = "ranked",
) -> None:
    await conn.execute(
        "UPDATE therapeutic_candidates SET rank_score=?, state=? WHERE id=?",
        (rank_score, state, candidate_id),
    )
    await conn.commit()


async def set_candidate_state(
    conn: aiosqlite.Connection, candidate_id: str, state: str
) -> None:
    await conn.execute(
        "UPDATE therapeutic_candidates SET state=? WHERE id=?",
        (state, candidate_id),
    )
    await conn.commit()


async def insert_candidate_evaluation(
    conn: aiosqlite.Connection, evaluation: TherapeuticCandidateEvaluation
) -> bool:
    cur = await conn.execute(
        """INSERT OR IGNORE INTO therapeutic_candidate_evaluations(
               id, candidate_id, session_id, created_at, overview, therapeutic_history,
               mechanism_of_action, expected_effect, overall_evaluation, artifact_path)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            evaluation.id,
            evaluation.candidate_id,
            evaluation.session_id,
            evaluation.created_at.isoformat(),
            evaluation.overview,
            evaluation.therapeutic_history,
            evaluation.mechanism_of_action,
            evaluation.expected_effect,
            evaluation.overall_evaluation,
            evaluation.artifact_path,
        ),
    )
    await conn.commit()
    return cur.rowcount > 0


async def list_candidate_evaluations(
    conn: aiosqlite.Connection, session_id: str
) -> list[TherapeuticCandidateEvaluation]:
    async with conn.execute(
        """SELECT * FROM therapeutic_candidate_evaluations
             WHERE session_id=? ORDER BY created_at ASC""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_candidate_evaluation(r) for r in rows]


async def insert_analysis_run(conn: aiosqlite.Connection, run: AnalysisRun) -> bool:
    cur = await conn.execute(
        """INSERT OR IGNORE INTO analysis_runs(
               id, session_id, created_at, kind, dataset_artifact_ids, trajectories,
               summary, artifact_path)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            run.id,
            run.session_id,
            run.created_at.isoformat(),
            run.kind,
            json.dumps(run.dataset_artifact_ids),
            run.trajectories,
            run.summary,
            run.artifact_path,
        ),
    )
    await conn.commit()
    return cur.rowcount > 0


async def list_analysis_runs(conn: aiosqlite.Connection, session_id: str) -> list[AnalysisRun]:
    async with conn.execute(
        "SELECT * FROM analysis_runs WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_analysis_run(r) for r in rows]


async def fetch_analysis_run(conn: aiosqlite.Connection, run_id: str) -> AnalysisRun | None:
    async with conn.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_analysis_run(row) if row else None


async def fetch_experiment_insight(
    conn: aiosqlite.Connection, insight_id: str
) -> ExperimentInsight | None:
    async with conn.execute("SELECT * FROM experiment_insights WHERE id=?", (insight_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_experiment_insight(row) if row else None


async def insert_experiment_insight(conn: aiosqlite.Connection, insight: ExperimentInsight) -> bool:
    cur = await conn.execute(
        """INSERT OR IGNORE INTO experiment_insights(
               id, session_id, analysis_run_id, created_at, summary, positive_hits,
               negative_hits, suggested_mechanisms, follow_up_assays, constraints_json,
               artifact_path)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            insight.id,
            insight.session_id,
            insight.analysis_run_id,
            insight.created_at.isoformat(),
            insight.summary,
            json.dumps(insight.positive_hits),
            json.dumps(insight.negative_hits),
            json.dumps(insight.suggested_mechanisms),
            json.dumps(insight.follow_up_assays),
            json.dumps(insight.constraints),
            insight.artifact_path,
        ),
    )
    await conn.commit()
    return cur.rowcount > 0


async def list_experiment_insights(
    conn: aiosqlite.Connection, session_id: str
) -> list[ExperimentInsight]:
    async with conn.execute(
        "SELECT * FROM experiment_insights WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_experiment_insight(r) for r in rows]


def _row_to_assay(row: aiosqlite.Row) -> AssayProposal:
    return AssayProposal(
        id=row["id"],
        session_id=row["session_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        round_index=row["round_index"],
        strategy_name=row["strategy_name"],
        reasoning=row["reasoning"],
        artifact_path=row["artifact_path"],
        rank_score=row["rank_score"],
        state=row["state"],
    )


def _row_to_assay_evaluation(row: aiosqlite.Row) -> AssayEvaluation:
    return AssayEvaluation(
        id=row["id"],
        assay_id=row["assay_id"],
        session_id=row["session_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        overview=row["overview"],
        biomedical_evidence=row["biomedical_evidence"],
        previous_use=row["previous_use"],
        overall_evaluation=row["overall_evaluation"],
        artifact_path=row["artifact_path"],
    )


def _row_to_candidate(row: aiosqlite.Row) -> TherapeuticCandidate:
    return TherapeuticCandidate(
        id=row["id"],
        session_id=row["session_id"],
        assay_id=row["assay_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        round_index=row["round_index"],
        candidate=row["candidate"],
        hypothesis=row["hypothesis"],
        reasoning=row["reasoning"],
        artifact_path=row["artifact_path"],
        rank_score=row["rank_score"],
        state=row["state"],
    )


def _row_to_candidate_evaluation(row: aiosqlite.Row) -> TherapeuticCandidateEvaluation:
    return TherapeuticCandidateEvaluation(
        id=row["id"],
        candidate_id=row["candidate_id"],
        session_id=row["session_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        overview=row["overview"],
        therapeutic_history=row["therapeutic_history"],
        mechanism_of_action=row["mechanism_of_action"],
        expected_effect=row["expected_effect"],
        overall_evaluation=row["overall_evaluation"],
        artifact_path=row["artifact_path"],
    )


def _row_to_analysis_run(row: aiosqlite.Row) -> AnalysisRun:
    return AnalysisRun(
        id=row["id"],
        session_id=row["session_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        kind=row["kind"],
        dataset_artifact_ids=json.loads(row["dataset_artifact_ids"]),
        trajectories=row["trajectories"],
        summary=row["summary"],
        artifact_path=row["artifact_path"],
    )


def _row_to_experiment_insight(row: aiosqlite.Row) -> ExperimentInsight:
    return ExperimentInsight(
        id=row["id"],
        session_id=row["session_id"],
        analysis_run_id=row["analysis_run_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        summary=row["summary"],
        positive_hits=json.loads(row["positive_hits"]),
        negative_hits=json.loads(row["negative_hits"]),
        suggested_mechanisms=json.loads(row["suggested_mechanisms"]),
        follow_up_assays=json.loads(row["follow_up_assays"]),
        constraints=json.loads(row["constraints_json"]),
        artifact_path=row["artifact_path"],
    )
