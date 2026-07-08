-- AI-powered review recommendations
-- Adds a JSONB column to ai_daily_reports for storing LLM-generated
-- review word lists. Purely additive migration.

ALTER TABLE ai_daily_reports ADD COLUMN IF NOT EXISTS review_recommendations JSONB NOT NULL DEFAULT '{}';
