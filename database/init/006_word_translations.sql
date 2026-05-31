CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS word_translations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    word VARCHAR(120) NOT NULL,
    chinese_text TEXT NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'llm',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_word_translations_user_word UNIQUE (user_id, word)
);

CREATE INDEX IF NOT EXISTS ix_word_translations_user_id ON word_translations(user_id);
CREATE INDEX IF NOT EXISTS ix_word_translations_course_id ON word_translations(course_id);
CREATE INDEX IF NOT EXISTS ix_word_translations_word ON word_translations(word);
