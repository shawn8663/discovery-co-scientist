-- AI Co-Scientist — SQLite schema (initial)
-- Apply via co_scientist.storage.db.init_db / migrate.
-- WAL is set at connection time, not here.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    status              TEXT NOT NULL,                 -- running|paused|done|failed|aborted
    workflow            TEXT NOT NULL DEFAULT 'general_hypothesis',
    research_goal       TEXT NOT NULL,
    research_plan       TEXT NOT NULL,                 -- JSON
    config_snapshot     TEXT NOT NULL,                 -- JSON (frozen config at start)
    budget_tokens       INTEGER NOT NULL,
    budget_usd          REAL NOT NULL,
    budget_used_tokens  INTEGER NOT NULL DEFAULT 0,
    budget_used_usd     REAL NOT NULL DEFAULT 0,
    wall_deadline       TEXT,
    final_overview      TEXT
);
CREATE INDEX IF NOT EXISTS sessions_status ON sessions(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS sessions_workflow ON sessions(workflow, updated_at DESC);

CREATE TABLE IF NOT EXISTS hypotheses (
    id              TEXT PRIMARY KEY,                  -- deterministic sha256 prefix
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,                     -- generation|evolution
    strategy        TEXT NOT NULL,                     -- literature|debate|combine|simplify|out_of_box|feasibility
    parent_ids      TEXT,                              -- JSON array
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    full_text       TEXT NOT NULL,
    artifact_path   TEXT NOT NULL,
    elo             REAL,
    matches_played  INTEGER NOT NULL DEFAULT 0,
    state           TEXT NOT NULL,                     -- draft|reviewed|in_tournament|pinned|rejected|quarantined|retired
    dedup_cluster   TEXT
);
CREATE INDEX IF NOT EXISTS hyp_sess_elo   ON hypotheses(session_id, elo DESC);
CREATE INDEX IF NOT EXISTS hyp_sess_state ON hypotheses(session_id, state);

CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,                  -- sha256(hyp_id || kind || iteration)
    hypothesis_id   TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    kind            TEXT NOT NULL,                     -- screen|full|verification|observation|simulation
    verdict         TEXT,                              -- already_explained|other_more_likely|missing_piece|neutral|disproved
    novelty         REAL,
    correctness     REAL,
    testability     REAL,
    feasibility     REAL,
    body            TEXT NOT NULL,
    artifact_path   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rev_hyp ON reviews(hypothesis_id, created_at DESC);
CREATE INDEX IF NOT EXISTS rev_sess ON reviews(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tournament_matches (
    id              TEXT PRIMARY KEY,                  -- sha256(min(a,b) || max(a,b) || round_id)
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    hyp_a           TEXT NOT NULL REFERENCES hypotheses(id),
    hyp_b           TEXT NOT NULL REFERENCES hypotheses(id),
    mode            TEXT NOT NULL,                     -- pairwise|debate|batch|invalid
    winner          TEXT,                              -- 'a'|'b'|NULL (invalid)
    elo_a_before    REAL NOT NULL,
    elo_b_before    REAL NOT NULL,
    elo_a_after     REAL,
    elo_b_after     REAL,
    rationale       TEXT,
    transcript_id   TEXT,
    similarity      REAL
);
CREATE INDEX IF NOT EXISTS mat_sess ON tournament_matches(session_id, created_at DESC);

-- Append-only Elo ledger. UNIQUE on match_id makes Elo updates idempotent.
CREATE TABLE IF NOT EXISTS elo_journal (
    update_id       TEXT PRIMARY KEY,                  -- = match.id
    match_id        TEXT UNIQUE NOT NULL,
    hyp_a           TEXT NOT NULL,
    hyp_b           TEXT NOT NULL,
    winner          TEXT NOT NULL,
    elo_a_before    REAL NOT NULL,
    elo_b_before    REAL NOT NULL,
    elo_a_after     REAL NOT NULL,
    elo_b_after     REAL NOT NULL,
    applied_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    finished_at         TEXT,
    agent               TEXT NOT NULL,
    action              TEXT NOT NULL,
    target_id           TEXT,
    payload             TEXT NOT NULL,                 -- JSON
    priority            INTEGER NOT NULL DEFAULT 100,
    status              TEXT NOT NULL,                 -- pending|leased|in_progress|done|failed|dead|cancelled
    lease_owner         TEXT,
    lease_expires_at    INTEGER,
    attempts            INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    idempotency_key     TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS tasks_queue ON tasks(session_id, status, priority, created_at);

CREATE TABLE IF NOT EXISTS transcripts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    task_id         TEXT REFERENCES tasks(id),
    agent           TEXT NOT NULL,
    action          TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read      INTEGER NOT NULL DEFAULT 0,
    cache_write     INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,
    finished_at     TEXT NOT NULL,
    artifact_path   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trn_sess ON transcripts(session_id, started_at DESC);
CREATE INDEX IF NOT EXISTS trn_task ON transcripts(task_id);

CREATE TABLE IF NOT EXISTS system_feedback (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    source          TEXT NOT NULL,                     -- human|meta_review
    kind            TEXT NOT NULL,                     -- directive|preference|rejection|pin|system_feedback
    target_id       TEXT,
    text            TEXT NOT NULL,
    artifact_path   TEXT,
    active          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS fb_sess_active ON system_feedback(session_id, active, created_at DESC);

CREATE TABLE IF NOT EXISTS embeddings_meta (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    hypothesis_id   TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    faiss_offset    INTEGER NOT NULL,
    text_hash       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(hypothesis_id, model)
);
CREATE INDEX IF NOT EXISTS emb_sess ON embeddings_meta(session_id);

-- Observability
CREATE TABLE IF NOT EXISTS spans (
    span_id         TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    parent_span_id  TEXT,
    session_id      TEXT,
    task_id         TEXT,
    name            TEXT NOT NULL,
    started_at      INTEGER NOT NULL,
    ended_at        INTEGER,
    attrs_json      TEXT,
    status          TEXT                                -- ok|error|unset
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS spans_task  ON spans(task_id);
CREATE INDEX IF NOT EXISTS spans_sess  ON spans(session_id, started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    session_id      TEXT,
    task_id         TEXT,
    agent           TEXT,
    event           TEXT NOT NULL,
    payload         TEXT                                -- JSON
);
CREATE INDEX IF NOT EXISTS events_sess ON events(session_id, ts DESC);
CREATE INDEX IF NOT EXISTS events_type ON events(event, ts DESC);

CREATE TABLE IF NOT EXISTS assay_proposals (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    round_index     INTEGER NOT NULL DEFAULT 1,
    strategy_name   TEXT NOT NULL,
    reasoning       TEXT NOT NULL,
    artifact_path   TEXT NOT NULL,
    rank_score      REAL,
    state           TEXT NOT NULL DEFAULT 'proposed'
);
CREATE INDEX IF NOT EXISTS assay_sess ON assay_proposals(session_id, round_index, created_at);

CREATE TABLE IF NOT EXISTS assay_evaluations (
    id                  TEXT PRIMARY KEY,
    assay_id            TEXT NOT NULL REFERENCES assay_proposals(id) ON DELETE CASCADE,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at          TEXT NOT NULL,
    overview            TEXT NOT NULL,
    biomedical_evidence TEXT NOT NULL,
    previous_use        TEXT NOT NULL,
    overall_evaluation  TEXT NOT NULL,
    artifact_path       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assay_eval_sess ON assay_evaluations(session_id, created_at);

CREATE TABLE IF NOT EXISTS therapeutic_candidates (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    assay_id        TEXT REFERENCES assay_proposals(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL,
    round_index     INTEGER NOT NULL DEFAULT 1,
    candidate       TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,
    reasoning       TEXT NOT NULL,
    artifact_path   TEXT NOT NULL,
    rank_score      REAL,
    state           TEXT NOT NULL DEFAULT 'proposed'
);
CREATE INDEX IF NOT EXISTS cand_sess ON therapeutic_candidates(session_id, round_index, created_at);

CREATE TABLE IF NOT EXISTS therapeutic_candidate_evaluations (
    id                  TEXT PRIMARY KEY,
    candidate_id        TEXT NOT NULL REFERENCES therapeutic_candidates(id) ON DELETE CASCADE,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at          TEXT NOT NULL,
    overview            TEXT NOT NULL,
    therapeutic_history TEXT NOT NULL,
    mechanism_of_action TEXT NOT NULL,
    expected_effect     TEXT NOT NULL,
    overall_evaluation  TEXT NOT NULL,
    artifact_path       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cand_eval_sess ON therapeutic_candidate_evaluations(session_id, created_at);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at           TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    dataset_artifact_ids TEXT NOT NULL,
    trajectories         INTEGER NOT NULL DEFAULT 3,
    summary              TEXT NOT NULL,
    artifact_path        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analysis_runs_sess ON analysis_runs(session_id, created_at);

CREATE TABLE IF NOT EXISTS experiment_insights (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    analysis_run_id      TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    created_at           TEXT NOT NULL,
    summary              TEXT NOT NULL,
    positive_hits        TEXT NOT NULL,
    negative_hits        TEXT NOT NULL,
    suggested_mechanisms TEXT NOT NULL,
    follow_up_assays     TEXT NOT NULL,
    constraints_json     TEXT NOT NULL,
    artifact_path        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS insights_sess ON experiment_insights(session_id, created_at);

-- Schema version tracking (linear migrations)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    name        TEXT NOT NULL
);
