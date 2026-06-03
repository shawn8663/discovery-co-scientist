-- Migration 0006: Robin-style therapeutic discovery workflow tables.

ALTER TABLE sessions ADD COLUMN workflow TEXT NOT NULL DEFAULT 'general_hypothesis';
CREATE INDEX IF NOT EXISTS sessions_workflow ON sessions(workflow, updated_at DESC);

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
