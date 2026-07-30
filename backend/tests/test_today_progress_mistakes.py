"""Tests for the mistakes card of build_today_progress (今日学习进度 · 强化错词).

Regression context (parent report 2026-07-30): the card showed
"28 done / 1696 remaining". Root causes: (a) planned counted RAW MistakeLog
rows — repeat failures of the same word each inserted an open row ("us" had
59), so 1,724 rows really meant 314 distinct words; (b) completed filtered on
occurred_at (when the mistake HAPPENED) instead of resolved_at (when it was
overcome); (c) the subtraction mixed a lifetime backlog with a today-scoped
counter. The card now tracks distinct open mistakes with the identity
remaining = planned - completed by construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.services.memory_dashboard import build_today_progress
from sqlalchemy.dialects import postgresql


def _compiled(stmt) -> str:
    # Lowercased: the PG dialect renders `IS false` / `distinct(...)`, so
    # case-sensitive substring checks would silently never match.
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


def _fake_db(*, open_mistakes: int, resolved_today: int) -> MagicMock:
    db = MagicMock()
    # _build_word_due_map: no due words → planned_reviews = 0.
    db.execute.return_value.all.return_value = []

    def _scalar(stmt):
        sql = _compiled(stmt)
        if "mistake_logs" in sql:
            if "is_resolved is false" in sql:
                return open_mistakes
            if "is_resolved is true" in sql:
                return resolved_today
        return 0

    db.scalar.side_effect = _scalar
    return db


class TestMistakesCard:
    def test_identity_remaining_equals_planned_minus_completed(self):
        db = _fake_db(open_mistakes=314, resolved_today=7)
        progress = build_today_progress(db, uuid4())
        mistakes = progress["mistakes"]
        assert mistakes["planned"] == 321  # 314 open + 7 resolved today
        assert mistakes["completed"] == 7
        assert mistakes["remaining"] == 314
        assert mistakes["remaining"] == mistakes["planned"] - mistakes["completed"]

    def test_zero_when_nothing_open(self):
        db = _fake_db(open_mistakes=0, resolved_today=3)
        mistakes = build_today_progress(db, uuid4())["mistakes"]
        assert mistakes == {"planned": 3, "completed": 3, "remaining": 0}

    def test_open_mistakes_counted_as_distinct_pairs_not_rows(self):
        # The query must dedup by (learning_item_id, expected_answer) — raw
        # row counting is the 1,724-rows-for-314-words bug.
        db = _fake_db(open_mistakes=1, resolved_today=0)
        build_today_progress(db, uuid4())
        mistake_sqls = [
            _compiled(call.args[0])
            for call in db.scalar.call_args_list
            if "mistake_logs" in _compiled(call.args[0])
        ]
        assert len(mistake_sqls) == 2
        for sql in mistake_sqls:
            assert "distinct" in sql
            assert "expected_answer" in sql

    def test_completed_uses_resolved_at_not_occurred_at(self):
        # "Resolved today" must key on resolved_at — occurred_at is when the
        # mistake HAPPENED (the old bug: a backlog mistake cleared today
        # never counted as progress).
        db = _fake_db(open_mistakes=1, resolved_today=0)
        build_today_progress(db, uuid4())
        resolved_sqls = [
            _compiled(call.args[0])
            for call in db.scalar.call_args_list
            if "is_resolved is true" in _compiled(call.args[0])
        ]
        assert len(resolved_sqls) == 1
        assert "resolved_at >=" in resolved_sqls[0]
