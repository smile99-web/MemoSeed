-- Learning Replay System: minute-level event tracking and statistics
-- Enables: heatmap, hour-level replay, minute-level breakdown, event log

-- Event-level log: one row per answer attempt
CREATE TABLE IF NOT EXISTS learning_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    learning_item_id UUID REFERENCES learning_items(id) ON DELETE SET NULL,
    review_log_id UUID REFERENCES review_logs(id) ON DELETE SET NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_date DATE NOT NULL,
    event_hour SMALLINT NOT NULL CHECK (event_hour >= 0 AND event_hour <= 23),
    event_minute SMALLINT NOT NULL CHECK (event_minute >= 0 AND event_minute <= 59),
    event_week SMALLINT NOT NULL,
    event_year SMALLINT NOT NULL,
    item_type VARCHAR(16) NOT NULL,
    review_mode VARCHAR(64),
    is_correct BOOLEAN,
    score SMALLINT,
    english_text TEXT NOT NULL,
    chinese_text TEXT,
    response_text TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error_type VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_learning_events_user_date ON learning_events(user_id, event_date);
CREATE INDEX IF NOT EXISTS idx_learning_events_user_hour ON learning_events(user_id, event_date, event_hour);
CREATE INDEX IF NOT EXISTS idx_learning_events_user_minute ON learning_events(user_id, event_date, event_hour, event_minute);
CREATE INDEX IF NOT EXISTS idx_learning_events_year ON learning_events(user_id, event_year);
CREATE INDEX IF NOT EXISTS idx_learning_events_mode ON learning_events(user_id, review_mode);

-- Pre-aggregated per-minute statistics for fast heatmap/histogram queries
CREATE TABLE IF NOT EXISTS learning_minute_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stat_date DATE NOT NULL,
    stat_hour SMALLINT NOT NULL,
    stat_minute SMALLINT NOT NULL,
    total_events INTEGER NOT NULL DEFAULT 0,
    spelling_events INTEGER NOT NULL DEFAULT 0,
    english_to_chinese_events INTEGER NOT NULL DEFAULT 0,
    chinese_to_english_events INTEGER NOT NULL DEFAULT 0,
    phrase_events INTEGER NOT NULL DEFAULT 0,
    sentence_events INTEGER NOT NULL DEFAULT 0,
    correct_events INTEGER NOT NULL DEFAULT 0,
    incorrect_events INTEGER NOT NULL DEFAULT 0,
    study_duration_ms BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, stat_date, stat_hour, stat_minute)
);

CREATE INDEX IF NOT EXISTS idx_minute_stats_user_date ON learning_minute_stats(user_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_minute_stats_user_hour ON learning_minute_stats(user_id, stat_date, stat_hour);
