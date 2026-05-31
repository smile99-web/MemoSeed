CREATE TABLE IF NOT EXISTS speech_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    provider VARCHAR(32) NOT NULL DEFAULT 'volcengine',
    language VARCHAR(16) NOT NULL,
    voice VARCHAR(120) NOT NULL,
    speech_rate INTEGER NOT NULL DEFAULT 0,
    text_hash VARCHAR(64) NOT NULL,
    text TEXT NOT NULL,
    audio_url TEXT NOT NULL,
    suffix VARCHAR(12) NOT NULL DEFAULT 'mp3',
    cached BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_speech_assets_user_voice_text UNIQUE (user_id, provider, language, voice, speech_rate, text_hash)
);

CREATE INDEX IF NOT EXISTS ix_speech_assets_user_id ON speech_assets(user_id);
CREATE INDEX IF NOT EXISTS ix_speech_assets_course_id ON speech_assets(course_id);
CREATE INDEX IF NOT EXISTS ix_speech_assets_text_hash ON speech_assets(text_hash);
CREATE INDEX IF NOT EXISTS ix_speech_assets_language ON speech_assets(language);
CREATE INDEX IF NOT EXISTS ix_speech_assets_voice ON speech_assets(voice);
