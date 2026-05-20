CREATE TABLE IF NOT EXISTS study_time_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_study_time_logs_user_id ON study_time_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_study_time_logs_course_id ON study_time_logs(course_id);
CREATE INDEX IF NOT EXISTS idx_study_time_logs_recorded_at ON study_time_logs(recorded_at);
