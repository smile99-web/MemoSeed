CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE courses ADD COLUMN IF NOT EXISTS prerequisite_course_id UUID;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS min_mastery_ratio DOUBLE PRECISION NOT NULL DEFAULT 0.75;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'courses_prerequisite_course_id_fkey'
    ) THEN
        ALTER TABLE courses
            ADD CONSTRAINT courses_prerequisite_course_id_fkey
            FOREIGN KEY (prerequisite_course_id) REFERENCES courses(id) ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE learning_items ADD COLUMN IF NOT EXISTS syllables JSON;
ALTER TABLE learning_items ADD COLUMN IF NOT EXISTS grapheme_phoneme_map JSON;
ALTER TABLE learning_items ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE learning_items ADD COLUMN IF NOT EXISTS unit_label VARCHAR(50);

ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS short_term_stability DOUBLE PRECISION DEFAULT 1.0;
ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS last_short_term_updated_at TIMESTAMPTZ;

ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS encoding_stage VARCHAR(32);
ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS encoding_duration_ms INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS generated_sentences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    focus_words_hash VARCHAR(64) NOT NULL,
    difficulty_level INTEGER NOT NULL DEFAULT 3,
    english_text TEXT NOT NULL,
    chinese_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tts_usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text_hash VARCHAR(64) NOT NULL,
    text_length INTEGER NOT NULL,
    voice VARCHAR(120) NOT NULL,
    speech_rate INTEGER NOT NULL DEFAULT 0,
    provider VARCHAR(32) NOT NULL,
    cached BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_courses_prerequisite_course_id ON courses(prerequisite_course_id);
CREATE INDEX IF NOT EXISTS idx_learning_items_sort_order ON learning_items(sort_order);
CREATE INDEX IF NOT EXISTS ix_generated_sentences_focus_words_hash ON generated_sentences(focus_words_hash);
CREATE INDEX IF NOT EXISTS ix_tts_usage_logs_user_id ON tts_usage_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_tts_usage_logs_text_hash ON tts_usage_logs(text_hash);
