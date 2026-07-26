"""Tests for the today_read_aloud_count field on the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.memory_dashboard import count_today_read_aloud
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _event(*, review_mode: str, hours_ago: float = 0):
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        review_mode=review_mode,
        occurred_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


class _ScalarResult:
    def __init__(self, value): self._value = value

    def scalar(self): return self._value


class _FakeDb:
    def __init__(self, count):
        self._count = count

    def scalar(self, _stmt):
        return self._count


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