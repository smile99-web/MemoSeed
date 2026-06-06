-- Points & reward system tables
-- Run this migration to enable the gamification features.

CREATE TABLE IF NOT EXISTS user_points (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    total_points INTEGER NOT NULL DEFAULT 0 CHECK (total_points >= 0),
    level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1 AND level <= 99),
    current_streak_days INTEGER NOT NULL DEFAULT 0,
    longest_streak_days INTEGER NOT NULL DEFAULT 0,
    last_awarded_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_points_user_id ON user_points(user_id);

CREATE TABLE IF NOT EXISTS points_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    points_changed INTEGER NOT NULL CHECK (points_changed != 0),
    reason VARCHAR(64) NOT NULL,
    detail TEXT,
    learning_item_id UUID REFERENCES learning_items(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_points_logs_user_id ON points_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_points_logs_reason ON points_logs(reason);
CREATE INDEX IF NOT EXISTS idx_points_logs_created_at ON points_logs(created_at);
