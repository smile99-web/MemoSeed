"""Tests for the today/week read-aloud count fields on the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.memory_dashboard import count_today_read_aloud, count_week_read_aloud
from app.services.speak_practice import ECHO_READ_REVIEW_MODE, READ_ALOUD_REVIEW_MODE
from sqlalchemy.dialects import postgresql
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _event(*, review_mode: str, hours_ago: float = 0):
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        review_mode=review_mode,
        occurred_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


class _FakeDb:
    def __init__(self, count):
        self._count = count

    def scalar(self, _stmt):
        return self._count


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class TestCountTodayReadAloud:
    def test_returns_db_scalar_value(self):
        # The function must just relay what the COUNT(*) returns, with a
        # safe int cast (db.scalar can return None on empty results).
        db = _FakeDb(7)
        now_local = datetime.now(LOCAL_TZ)
        assert count_today_read_aloud(db, uuid4(), now_local) == 7

    def test_returns_zero_on_empty(self):
        db = _FakeDb(None)
        assert count_today_read_aloud(db, uuid4()) == 0

    def test_now_local_falls_back_to_local_tz(self):
        # No now_local argument → uses Asia/Shanghai "now". The function
        # should still produce an integer.
        db = _FakeDb(0)
        assert count_today_read_aloud(db, uuid4()) == 0

    def test_counts_both_speak_mode_and_exercise_echo_events(self):
        # 今日朗读次数 must cover every read-aloud: the dedicated 语音练习
        # queue ("read-aloud") AND the echo gate after ordinary exercises
        # ("echo-read"). Speak-mode-only counting is the bug the parent
        # reported (child reads all day, card shows 0).
        db = MagicMock()
        db.scalar.return_value = 3
        count_today_read_aloud(db, uuid4(), datetime.now(LOCAL_TZ))
        stmt = db.scalar.call_args[0][0]
        sql = _compiled(stmt)
        assert f"'{READ_ALOUD_REVIEW_MODE}'" in sql
        assert f"'{ECHO_READ_REVIEW_MODE}'" in sql
        assert " IN " in sql


class TestCountWeekReadAloud:
    def test_week_starts_on_monday_local(self):
        # Wednesday 2026-07-22 15:00 Asia/Shanghai → week starts Mon 07-20
        # 00:00 Shanghai = 2026-07-19 16:00 UTC.
        db = MagicMock()
        db.scalar.return_value = 5
        wednesday = datetime(2026, 7, 22, 15, 0, tzinfo=LOCAL_TZ)
        assert count_week_read_aloud(db, uuid4(), wednesday) == 5
        stmt = db.scalar.call_args[0][0]
        sql = _compiled(stmt)
        assert "2026-07-19 16:00:00" in sql

    def test_sunday_counts_toward_the_same_week(self):
        # Sunday belongs to the week that started the previous Monday.
        db = MagicMock()
        db.scalar.return_value = 0
        sunday = datetime(2026, 7, 26, 10, 0, tzinfo=LOCAL_TZ)
        count_week_read_aloud(db, uuid4(), sunday)
        sql = _compiled(db.scalar.call_args[0][0])
        assert "2026-07-19 16:00:00" in sql  # Mon 07-20 00:00 +08 → 07-19 16:00 UTC

    def test_counts_both_modes(self):
        db = MagicMock()
        db.scalar.return_value = 0
        count_week_read_aloud(db, uuid4(), datetime.now(LOCAL_TZ))
        sql = _compiled(db.scalar.call_args[0][0])
        assert f"'{READ_ALOUD_REVIEW_MODE}'" in sql
        assert f"'{ECHO_READ_REVIEW_MODE}'" in sql

    def test_returns_zero_on_empty(self):
        db = _FakeDb(None)
        assert count_week_read_aloud(db, uuid4()) == 0


class TestModeConstants:
    def test_echo_read_is_distinct_from_speak_mode(self):
        # The speak queue's "already spoken today" exclusion keys on
        # "read-aloud" only — echo-read must stay a separate mode so review
        # echoes never shrink the speak queue.
        assert ECHO_READ_REVIEW_MODE != READ_ALOUD_REVIEW_MODE