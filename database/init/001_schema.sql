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

CREATE TABLE IF NOT EXISTS user_model_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS course_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package_id UUID NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (package_id, name)
);

CREATE TABLE IF NOT EXISTS learning_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
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
    consecutive_correct_count INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_correct_count >= 0),
    consecutive_error_count INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_error_count >= 0),
    recall_correct_count INTEGER NOT NULL DEFAULT 0 CHECK (recall_correct_count >= 0),
    hinted_correct_count INTEGER NOT NULL DEFAULT 0 CHECK (hinted_correct_count >= 0),
    preview_correct_count INTEGER NOT NULL DEFAULT 0 CHECK (preview_correct_count >= 0),
    context_correct_count INTEGER NOT NULL DEFAULT 0 CHECK (context_correct_count >= 0),
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
    error_type VARCHAR(32),
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
    error_type VARCHAR(32),
    expected_answer TEXT NOT NULL,
    actual_answer TEXT NOT NULL,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS word_memory_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word VARCHAR(120) NOT NULL,
    learning_item_id UUID REFERENCES learning_items(id) ON DELETE SET NULL,
    memory_state_id UUID REFERENCES memory_states(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'teaching',
    memory_strength DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    forget_risk DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    priority_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    consecutive_correct_count INTEGER NOT NULL DEFAULT 0,
    consecutive_error_count INTEGER NOT NULL DEFAULT 0,
    recall_correct_count INTEGER NOT NULL DEFAULT 0,
    hinted_correct_count INTEGER NOT NULL DEFAULT 0,
    preview_correct_count INTEGER NOT NULL DEFAULT 0,
    context_correct_count INTEGER NOT NULL DEFAULT 0,
    hidden_recall_correct_count INTEGER NOT NULL DEFAULT 0,
    no_hint_correct_date_count INTEGER NOT NULL DEFAULT 0,
    last_no_hint_correct_date DATE,
    last_answer_seen_at TIMESTAMPTZ,
    error_type_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    task_type_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_micro_review_at TIMESTAMPTZ,
    micro_review_stage INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, word)
);

CREATE TABLE IF NOT EXISTS word_review_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_memory_state_id UUID REFERENCES word_memory_states(id) ON DELETE CASCADE,
    learning_item_id UUID REFERENCES learning_items(id) ON DELETE SET NULL,
    word VARCHAR(120) NOT NULL,
    task_type VARCHAR(40) NOT NULL,
    prompt_text TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    choices JSONB NOT NULL DEFAULT '[]'::jsonb,
    priority_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    source VARCHAR(120) NOT NULL DEFAULT 'word-memory',
    due_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

CREATE TABLE IF NOT EXISTS study_time_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS course_completion_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
    correct_word_count INTEGER NOT NULL DEFAULT 0 CHECK (correct_word_count >= 0),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_token_hash ON refresh_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at ON refresh_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_user_model_settings_user_id ON user_model_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_course_packages_user_id ON course_packages(user_id);
CREATE INDEX IF NOT EXISTS idx_courses_user_id ON courses(user_id);
CREATE INDEX IF NOT EXISTS idx_courses_package_id ON courses(package_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_items_user_course_english_type ON learning_items(user_id, course_id, lower(english_text), item_type);
CREATE INDEX IF NOT EXISTS idx_learning_items_user_id ON learning_items(user_id);
CREATE INDEX IF NOT EXISTS idx_learning_items_course_id ON learning_items(course_id);
CREATE INDEX IF NOT EXISTS idx_learning_items_item_type ON learning_items(item_type);
CREATE INDEX IF NOT EXISTS idx_memory_states_next_review_at ON memory_states(next_review_at);
CREATE INDEX IF NOT EXISTS idx_review_logs_user_id ON review_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_learning_item_id ON review_logs(learning_item_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_reviewed_at ON review_logs(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_mistake_logs_user_id ON mistake_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_mistake_logs_learning_item_id ON mistake_logs(learning_item_id);
CREATE INDEX IF NOT EXISTS idx_mistake_logs_occurred_at ON mistake_logs(occurred_at);
CREATE INDEX IF NOT EXISTS idx_word_memory_states_user_id ON word_memory_states(user_id);
CREATE INDEX IF NOT EXISTS idx_word_memory_states_word ON word_memory_states(word);
CREATE INDEX IF NOT EXISTS idx_word_memory_states_next_micro_review_at ON word_memory_states(next_micro_review_at);
CREATE INDEX IF NOT EXISTS idx_word_review_tasks_user_id ON word_review_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_word_review_tasks_due_at ON word_review_tasks(due_at);
CREATE INDEX IF NOT EXISTS idx_word_review_tasks_status ON word_review_tasks(status);
CREATE INDEX IF NOT EXISTS idx_word_review_tasks_word ON word_review_tasks(word);
CREATE INDEX IF NOT EXISTS idx_daily_plans_user_date ON daily_plans(user_id, plan_date);
CREATE INDEX IF NOT EXISTS idx_ai_daily_reports_user_date ON ai_daily_reports(user_id, report_date);
CREATE INDEX IF NOT EXISTS idx_study_time_logs_user_id ON study_time_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_study_time_logs_course_id ON study_time_logs(course_id);
CREATE INDEX IF NOT EXISTS idx_study_time_logs_recorded_at ON study_time_logs(recorded_at);
CREATE INDEX IF NOT EXISTS idx_course_completion_logs_user_id ON course_completion_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_course_completion_logs_course_id ON course_completion_logs(course_id);
CREATE INDEX IF NOT EXISTS idx_course_completion_logs_completed_at ON course_completion_logs(completed_at);
