-- 2026-08-11: 每日一测三关重构 —— 标记复习来源场景。
-- 测试内提交（听音选中文/英选中/手写）与日常复习共用 review_mode，
-- "今日已测不重出"的排除逻辑需要独立标记。NULL = 日常学习。
ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS context VARCHAR(16);
CREATE INDEX IF NOT EXISTS idx_review_logs_context ON review_logs(context);
