CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    username VARCHAR(80) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS learning_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_type VARCHAR(32) NOT NULL CHECK (item_type IN ('word', 'phrase', 'sentence')),
    english_text TEXT NOT NULL,
    chinese_text TEXT NOT NULL,
    phonetic VARCHAR(255),
    difficulty_level INTEGER NOT NULL DEFAULT 1 CHECK (difficulty_level BETWEEN 1 AND 5),
    source VARCHAR(120),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    learning_item_id UUID NOT NULL UNIQUE REFERENCES learning_items(id) ON DELETE CASCADE,
    interval_days INTEGER NOT NULL DEFAULT 0 CHECK (interval_days >= 0),
    ease_factor DOUBLE PRECISION NOT NULL DEFAULT 2.5 CHECK (ease_factor >= 1.3),
    memory_strength DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (memory_strength >= 0.0 AND memory_strength <= 1.0),
    forget_risk DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (forget_risk >= 0.0 AND forget_risk <= 1.0),
    repetition_count INTEGER NOT NULL DEFAULT 0 CHECK (repetition_count >= 0),
    lapse_count INTEGER NOT NULL DEFAULT 0 CHECK (lapse_count >= 0),
    last_reviewed_at TIMESTAMPTZ,
    next_review_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS review_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    learning_item_id UUID NOT NULL REFERENCES learning_items(id) ON DELETE CASCADE,
    review_mode VARCHAR(32) NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 5),
    is_correct BOOLEAN NOT NULL,
    response_text TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mistake_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    learning_item_id UUID NOT NULL REFERENCES learning_items(id) ON DELETE CASCADE,
    mistake_type VARCHAR(64) NOT NULL,
    expected_answer TEXT NOT NULL,
    actual_answer TEXT NOT NULL,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_date DATE NOT NULL,
    warmup_review_minutes INTEGER NOT NULL DEFAULT 10 CHECK (warmup_review_minutes >= 0),
    new_learning_minutes INTEGER NOT NULL DEFAULT 20 CHECK (new_learning_minutes >= 0),
    sentence_training_minutes INTEGER NOT NULL DEFAULT 20 CHECK (sentence_training_minutes >= 0),
    mistake_reinforcement_minutes INTEGER NOT NULL DEFAULT 10 CHECK (mistake_reinforcement_minutes >= 0),
    new_word_limit INTEGER NOT NULL DEFAULT 0 CHECK (new_word_limit >= 0),
    new_phrase_limit INTEGER NOT NULL DEFAULT 0 CHECK (new_phrase_limit >= 0),
    strategy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, plan_date)
);

CREATE TABLE IF NOT EXISTS ai_daily_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_date DATE NOT NULL,
    accuracy_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (accuracy_rate >= 0.0 AND accuracy_rate <= 1.0),
    spelling_error_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (spelling_error_rate >= 0.0 AND spelling_error_rate <= 1.0),
    sentence_error_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (sentence_error_rate >= 0.0 AND sentence_error_rate <= 1.0),
    study_duration_minutes INTEGER NOT NULL DEFAULT 0 CHECK (study_duration_minutes >= 0),
    review_backlog_count INTEGER NOT NULL DEFAULT 0 CHECK (review_backlog_count >= 0),
    high_forget_risk_count INTEGER NOT NULL DEFAULT 0 CHECK (high_forget_risk_count >= 0),
    summary TEXT NOT NULL DEFAULT '',
    next_day_strategy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_token_hash ON refresh_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at ON refresh_tokens(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_items_user_english_type ON learning_items(user_id, lower(english_text), item_type);
CREATE INDEX IF NOT EXISTS idx_learning_items_user_id ON learning_items(user_id);
CREATE INDEX IF NOT EXISTS idx_learning_items_item_type ON learning_items(item_type);
CREATE INDEX IF NOT EXISTS idx_memory_states_next_review_at ON memory_states(next_review_at);
CREATE INDEX IF NOT EXISTS idx_review_logs_user_id ON review_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_learning_item_id ON review_logs(learning_item_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_reviewed_at ON review_logs(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_mistake_logs_user_id ON mistake_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_mistake_logs_learning_item_id ON mistake_logs(learning_item_id);
CREATE INDEX IF NOT EXISTS idx_mistake_logs_occurred_at ON mistake_logs(occurred_at);
CREATE INDEX IF NOT EXISTS idx_daily_plans_user_date ON daily_plans(user_id, plan_date);
CREATE INDEX IF NOT EXISTS idx_ai_daily_reports_user_date ON ai_daily_reports(user_id, report_date);
