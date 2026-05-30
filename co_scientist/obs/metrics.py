"""Read-only metrics aggregations for the web UI dashboards.

All counts roll up from existing tables (transcripts, tournament_matches, etc.)
— no separate metrics store. Keep queries small and indexed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import aiosqlite


@dataclass
class SessionMetrics:
    n_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    cache_hit_ratio: float | None = None
    n_matches: int = 0
    n_invalid_matches: int = 0
    n_hypotheses: int = 0
    n_in_tournament: int = 0
    n_reviewed: int = 0
    n_hypothesis_attempts: int = 0
    n_duplicate_hypotheses: int = 0
    n_deterministic_duplicates: int = 0
    n_semantic_duplicates: int = 0
    n_clustered_duplicates_retired: int = 0
    n_duplicates_reaching_tournament: int = 0
    duplicate_rate: float | None = None
    tournament_duplicate_rate: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    tools_called: int = 0
    tool_errors: int = 0
    retrieval_tool_calls: int = 0
    retrieval_cache_hits: int = 0
    retrieval_cache_misses: int = 0
    retrieval_cache_hit_ratio: float | None = None
    retrieval_latency_ms_total: int = 0
    retrieval_latency_ms_avg: float | None = None
    retrieval_sources: dict[str, dict[str, float | int]] = field(default_factory=dict)
    dead_tasks: int = 0


async def session_metrics(conn: aiosqlite.Connection, session_id: str) -> SessionMetrics:
    out = SessionMetrics()

    # LLM usage (from transcripts)
    async with conn.execute(
        """SELECT
              COUNT(*)                    AS n_calls,
              COALESCE(SUM(input_tokens),0)  AS input_tokens,
              COALESCE(SUM(output_tokens),0) AS output_tokens,
              COALESCE(SUM(cache_read),0)    AS cache_read,
              COALESCE(SUM(cache_write),0)   AS cache_write,
              COALESCE(SUM(cost_usd),0.0)    AS cost_usd
           FROM transcripts WHERE session_id=?""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        out.n_calls = row["n_calls"]
        out.input_tokens = row["input_tokens"]
        out.output_tokens = row["output_tokens"]
        out.cache_read = row["cache_read"]
        out.cache_write = row["cache_write"]
        out.cost_usd = float(row["cost_usd"])
        denom = out.cache_read + out.cache_write + out.input_tokens
        if denom > 0:
            out.cache_hit_ratio = out.cache_read / denom

    # Matches
    async with conn.execute(
        """SELECT
              SUM(CASE WHEN mode != 'invalid' THEN 1 ELSE 0 END) AS valid,
              SUM(CASE WHEN mode  = 'invalid' THEN 1 ELSE 0 END) AS invalid
           FROM tournament_matches WHERE session_id=?""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        out.n_matches = row["valid"] or 0
        out.n_invalid_matches = row["invalid"] or 0

    # Hypotheses
    async with conn.execute(
        """SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN state IN ('in_tournament','pinned') THEN 1 ELSE 0 END) AS in_tournament,
              SUM(CASE WHEN state IN ('reviewed','in_tournament','pinned') THEN 1 ELSE 0 END) AS reviewed
           FROM hypotheses WHERE session_id=?""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        out.n_hypotheses = row["total"] or 0
        out.n_in_tournament = row["in_tournament"] or 0
        out.n_reviewed = row["reviewed"] or 0

    # Duplicate suppression. Pre-insert deterministic/semantic duplicates are
    # counted from events; clustered duplicates are visible as retired
    # hypotheses, with events providing an agent-level breadcrumb.
    async with conn.execute(
        """SELECT
              SUM(CASE WHEN payload LIKE '%"reason": "deterministic"%' THEN 1 ELSE 0 END) AS deterministic,
              SUM(CASE WHEN payload LIKE '%"reason": "semantic"%' THEN 1 ELSE 0 END) AS semantic,
              SUM(CASE WHEN payload LIKE '%"reason": "clustered"%' THEN 1 ELSE 0 END) AS clustered
           FROM events
          WHERE session_id=? AND event='hypothesis_duplicate_suppressed'""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        out.n_deterministic_duplicates = row["deterministic"] or 0
        out.n_semantic_duplicates = row["semantic"] or 0
        out.n_clustered_duplicates_retired = row["clustered"] or 0

    if out.n_clustered_duplicates_retired == 0:
        async with conn.execute(
            """SELECT COUNT(*) AS n FROM hypotheses
                  WHERE session_id=?
                    AND state='retired'
                    AND dedup_cluster IS NOT NULL""",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            out.n_clustered_duplicates_retired = row["n"] or 0

    async with conn.execute(
        """SELECT COUNT(*) AS n
             FROM hypotheses AS h
            WHERE h.session_id=?
              AND h.dedup_cluster IS NOT NULL
              AND h.state IN ('in_tournament','pinned')
              AND EXISTS (
                    SELECT 1 FROM hypotheses AS other
                     WHERE other.session_id=h.session_id
                       AND other.dedup_cluster=h.dedup_cluster
                       AND other.id != h.id
              )""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        out.n_duplicates_reaching_tournament = row["n"] or 0

    out.n_duplicate_hypotheses = (
        out.n_deterministic_duplicates
        + out.n_semantic_duplicates
        + out.n_clustered_duplicates_retired
    )
    out.n_hypothesis_attempts = out.n_hypotheses + out.n_deterministic_duplicates + out.n_semantic_duplicates
    if out.n_hypothesis_attempts > 0:
        out.duplicate_rate = out.n_duplicate_hypotheses / out.n_hypothesis_attempts
    if out.n_in_tournament > 0:
        out.tournament_duplicate_rate = out.n_duplicates_reaching_tournament / out.n_in_tournament

    # Latency P50/P95 from transcripts (rough approximation using
    # finished_at - started_at parsed by SQLite's julianday function).
    async with conn.execute(
        """SELECT (strftime('%s', finished_at) - strftime('%s', started_at)) * 1000 AS dur_ms
              FROM transcripts WHERE session_id=? ORDER BY dur_ms""",
        (session_id,),
    ) as cur:
        durations = [r["dur_ms"] for r in await cur.fetchall() if r["dur_ms"] is not None]
    if durations:
        out.p50_latency_ms = _percentile(durations, 0.50)
        out.p95_latency_ms = _percentile(durations, 0.95)

    # Tool calls + errors (from events)
    async with conn.execute(
        """SELECT
              SUM(CASE WHEN event='tool_call' THEN 1 ELSE 0 END) AS tools_called,
              SUM(CASE WHEN event='tool_call' AND payload LIKE '%"is_error": true%' THEN 1 ELSE 0 END) AS tool_errors
           FROM events WHERE session_id=?""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        out.tools_called = row["tools_called"] or 0
        out.tool_errors = row["tool_errors"] or 0

    async with conn.execute(
        """SELECT payload FROM events
            WHERE session_id=? AND event='tool_call' AND payload IS NOT NULL""",
        (session_id,),
    ) as cur:
        tool_event_rows = await cur.fetchall()
    for event_row in tool_event_rows:
        try:
            payload = json.loads(event_row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            continue
        source = metadata.get("retrieval_source")
        if not isinstance(source, str) or not source:
            continue
        duration_ms = int(payload.get("duration_ms") or 0)
        hits, misses = _cache_counts(metadata)
        stats = out.retrieval_sources.setdefault(
            source,
            {
                "calls": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "latency_ms_total": 0,
                "latency_ms_avg": 0.0,
            },
        )
        stats["calls"] += 1
        stats["cache_hits"] += hits
        stats["cache_misses"] += misses
        stats["latency_ms_total"] += duration_ms
        stats["latency_ms_avg"] = stats["latency_ms_total"] / stats["calls"]
        out.retrieval_tool_calls += 1
        out.retrieval_cache_hits += hits
        out.retrieval_cache_misses += misses
        out.retrieval_latency_ms_total += duration_ms
    if out.retrieval_tool_calls > 0:
        out.retrieval_latency_ms_avg = out.retrieval_latency_ms_total / out.retrieval_tool_calls
    retrieval_cache_total = out.retrieval_cache_hits + out.retrieval_cache_misses
    if retrieval_cache_total > 0:
        out.retrieval_cache_hit_ratio = out.retrieval_cache_hits / retrieval_cache_total

    # Dead-lettered tasks
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE session_id=? AND status='dead'",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        out.dead_tasks = row["n"]

    return out


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    i = max(0, min(len(sorted_values) - 1, round(p * (len(sorted_values) - 1))))
    return float(sorted_values[i])


def _cache_counts(metadata: dict[str, Any]) -> tuple[int, int]:
    if "cache_hits" in metadata or "cache_misses" in metadata:
        return int(metadata.get("cache_hits") or 0), int(metadata.get("cache_misses") or 0)
    if metadata.get("cache_hit") is True:
        return 1, 0
    if metadata.get("cache_hit") is False:
        return 0, 1
    return 0, 0


@dataclass
class LeaderboardEntry:
    hypothesis_id: str
    title: str
    elo: float | None
    matches_played: int


async def leaderboard(
    conn: aiosqlite.Connection, session_id: str, k: int = 10
) -> list[LeaderboardEntry]:
    async with conn.execute(
        """SELECT id, title, elo, matches_played FROM hypotheses
              WHERE session_id=? AND state IN ('in_tournament','pinned')
              ORDER BY elo DESC NULLS LAST LIMIT ?""",
        (session_id, k),
    ) as cur:
        rows = await cur.fetchall()
    return [
        LeaderboardEntry(
            hypothesis_id=r["id"], title=r["title"], elo=r["elo"],
            matches_played=r["matches_played"],
        )
        for r in rows
    ]


# Tiny in-memory cache to dampen UI hot-polling.
_CACHE: dict[str, tuple[float, SessionMetrics]] = {}


async def session_metrics_cached(
    conn: aiosqlite.Connection, session_id: str, *, ttl_s: float = 1.0
) -> SessionMetrics:
    now = time.monotonic()
    hit = _CACHE.get(session_id)
    if hit is not None and now - hit[0] < ttl_s:
        return hit[1]
    m = await session_metrics(conn, session_id)
    _CACHE[session_id] = (now, m)
    return m


def to_dict(m: SessionMetrics) -> dict[str, Any]:
    return {
        "n_calls": m.n_calls,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "cache_read": m.cache_read,
        "cache_write": m.cache_write,
        "cost_usd": m.cost_usd,
        "cache_hit_ratio": m.cache_hit_ratio,
        "n_matches": m.n_matches,
        "n_invalid_matches": m.n_invalid_matches,
        "n_hypotheses": m.n_hypotheses,
        "n_in_tournament": m.n_in_tournament,
        "n_reviewed": m.n_reviewed,
        "n_hypothesis_attempts": m.n_hypothesis_attempts,
        "n_duplicate_hypotheses": m.n_duplicate_hypotheses,
        "n_deterministic_duplicates": m.n_deterministic_duplicates,
        "n_semantic_duplicates": m.n_semantic_duplicates,
        "n_clustered_duplicates_retired": m.n_clustered_duplicates_retired,
        "n_duplicates_reaching_tournament": m.n_duplicates_reaching_tournament,
        "duplicate_rate": m.duplicate_rate,
        "tournament_duplicate_rate": m.tournament_duplicate_rate,
        "p50_latency_ms": m.p50_latency_ms,
        "p95_latency_ms": m.p95_latency_ms,
        "tools_called": m.tools_called,
        "tool_errors": m.tool_errors,
        "retrieval_tool_calls": m.retrieval_tool_calls,
        "retrieval_cache_hits": m.retrieval_cache_hits,
        "retrieval_cache_misses": m.retrieval_cache_misses,
        "retrieval_cache_hit_ratio": m.retrieval_cache_hit_ratio,
        "retrieval_latency_ms_total": m.retrieval_latency_ms_total,
        "retrieval_latency_ms_avg": m.retrieval_latency_ms_avg,
        "retrieval_sources": m.retrieval_sources,
        "dead_tasks": m.dead_tasks,
    }
