-- FSRS audit fields for review_logs and memory_states.
-- Goal: make every scheduler decision auditable from the row level.
-- See docs/fsrs_verification_report.md (2026-06-17).
--
-- All columns are NULLABLE on purpose: this is a purely additive migration
-- that does not break existing rows. A separate backfill script
-- (backend/scripts/backfill_fsrs_audit.py) can populate historical rows
-- from user_model_settings.

ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS scheduler_type VARCHAR(32);
ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS algorithm_version VARCHAR(16);
ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS fsrs_params_snapshot JSONB;
ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS previous_interval INTEGER;
ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS new_interval INTEGER;
ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS next_review_at TIMESTAMPTZ;

ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS scheduler_type VARCHAR(32);
ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS algorithm_version VARCHAR(16);
ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS fsrs_params_snapshot JSONB;

-- Light indexes to enable filtering by scheduler type without full table scans.
CREATE INDEX IF NOT EXISTS ix_review_logs_scheduler_type ON review_logs(scheduler_type);
CREATE INDEX IF NOT EXISTS ix_memory_states_scheduler_type ON memory_states(scheduler_type);
