-- Grammar practice history tables
-- Stores per-session and per-answer records so the dashboard can show
-- "you've completed N grammar sets, average accuracy X%".
--
-- This is purely additive — does not modify any existing table.
-- Run after 009_fsrs_audit_fields.sql.

CREATE TABLE IF NOT EXISTS grammar_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    level INTEGER NOT NULL CHECK (level >= 1 AND level <= 10),
    total_questions INTEGER NOT NULL CHECK (total_questions > 0),
    correct_count INTEGER NOT NULL DEFAULT 0 CHECK (correct_count >= 0),
    choice_questions INTEGER NOT NULL DEFAULT 0 CHECK (choice_questions >= 0),
    fill_in_questions INTEGER NOT NULL DEFAULT 0 CHECK (fill_in_questions >= 0),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_grammar_sessions_user_id ON grammar_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_grammar_sessions_user_level ON grammar_sessions(user_id, level);
CREATE INDEX IF NOT EXISTS idx_grammar_sessions_completed_at ON grammar_sessions(user_id, completed_at);

CREATE TABLE IF NOT EXISTS grammar_answers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES grammar_sessions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id VARCHAR(64) NOT NULL,
    question_type VARCHAR(16) NOT NULL CHECK (question_type IN ('choice', 'fill_in_blank')),
    level INTEGER NOT NULL CHECK (level >= 1 AND level <= 10),
    prompt TEXT NOT NULL,
    user_answer TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL,
    time_spent_ms INTEGER NOT NULL DEFAULT 0 CHECK (time_spent_ms >= 0),
    answered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_grammar_answers_session_id ON grammar_answers(session_id);
CREATE INDEX IF NOT EXISTS idx_grammar_answers_user_id ON grammar_answers(user_id);
CREATE INDEX IF NOT EXISTS idx_grammar_answers_user_level ON grammar_answers(user_id, level);
