"""Unit tests for Learning Replay System."""

import pytest
from datetime import datetime, timezone, date, timedelta

from app.services.learning_replay import (
    COLOR_LEVELS,
    STUDY_SESSION_EDGE_GRACE_SECONDS,
    STUDY_SESSION_GAP_SECONDS,
    _build_study_windows,
    _filter_heartbeats_by_windows,
    categorize_review_mode,
    color_for_minutes,
)

UTC = timezone.utc
T0 = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


def _beats(start: datetime, count: int, step_s: int = 10, dur: float = 10.0):
    """count heartbeats spaced step_s seconds apart starting at start."""
    return [(start + timedelta(seconds=i * step_s), dur) for i in range(count)]


class TestStudySessionWindows:
    """学习时长"真实有效"口径：心跳只在学习事件锚定的会话窗口内计入。"""

    def test_heartbeats_near_event_counted(self):
        events = [T0]
        windows = _build_study_windows(events)
        beats = _beats(T0 - timedelta(seconds=100), 30)  # -100s .. +190s
        kept = _filter_heartbeats_by_windows(beats, windows)
        # 2026-08-07 收紧后 window = [T0-45s, T0+45s] → beats at -40..+40 kept (9 beats)
        assert len(kept) == 9
        assert sum(d for _, d in kept) == 90.0

    def test_heartbeats_beyond_edge_grace_dropped(self):
        events = [T0]
        windows = _build_study_windows(events)
        beats = _beats(T0 - timedelta(seconds=600), 120)  # -600s .. +590s
        kept = _filter_heartbeats_by_windows(beats, windows)
        assert all(abs((ts - T0).total_seconds()) <= STUDY_SESSION_EDGE_GRACE_SECONDS for ts, _ in kept)

    def test_gap_under_120s_merges_into_one_session(self):
        # 事件间隔 100s < 120s → 同一段学习，间隙心跳全计
        events = [T0, T0 + timedelta(seconds=100)]
        windows = _build_study_windows(events)
        assert len(windows) == 1
        beats = _beats(T0 + timedelta(seconds=10), 9)  # 间隙内 10s..90s
        kept = _filter_heartbeats_by_windows(beats, windows)
        assert len(kept) == 9

    def test_gap_over_120s_splits_and_interior_dropped(self):
        # 事件间隔 600s ≥ 120s → 两段会话，间隙超出 ±45s 宽限的部分剔除
        events = [T0, T0 + timedelta(seconds=600)]
        windows = _build_study_windows(events)
        assert len(windows) == 2
        beats = _beats(T0, 61)  # 0s .. 600s 每 10s 一条
        kept = _filter_heartbeats_by_windows(beats, windows)
        kept_times = [ts for ts, _ in kept]
        # 第一段窗口 [−45s, +45s]，第二段 [+555s, +645s]
        assert all(ts <= T0 + timedelta(seconds=STUDY_SESSION_EDGE_GRACE_SECONDS)
                   or ts >= T0 + timedelta(seconds=600 - STUDY_SESSION_EDGE_GRACE_SECONDS)
                   for ts in kept_times)
        # 中间 50s..550s 的心跳全部被剔除
        assert not any(T0 + timedelta(seconds=50) <= ts <= T0 + timedelta(seconds=550) for ts in kept_times)

    def test_gap_200s_now_splits(self):
        # 2026-08-07 收紧：旧口径 200s < 300s 还算同一段；新口径 ≥120s 即分段。
        # 家长实测：52 分钟计时里 20 分钟零答题，正是 300s 容差吞入空分钟所致。
        events = [T0, T0 + timedelta(seconds=200)]
        windows = _build_study_windows(events)
        assert len(windows) == 2

    def test_gap_exactly_at_threshold_splits(self):
        # 拆分语义是 strict <：恰好 120s 也拆。后续调参最容易改错的边界，锁死。
        events = [T0, T0 + timedelta(seconds=STUDY_SESSION_GAP_SECONDS)]
        windows = _build_study_windows(events)
        assert len(windows) == 2

    def test_gap_one_second_under_threshold_merges(self):
        events = [T0, T0 + timedelta(seconds=STUDY_SESSION_GAP_SECONDS - 1)]
        windows = _build_study_windows(events)
        assert len(windows) == 1

    def test_real_0729_pattern(self):
        # 复刻 2026-07-29 20 点档：连续心跳，但 24:36→33:18（522s）
        # 零事件 → 间隙只保留两端各 45s，中间全部剔除
        e1 = T0
        e2 = T0 + timedelta(seconds=522)
        windows = _build_study_windows([e1, e2])
        beats = _beats(T0 - timedelta(seconds=300), 90)  # -300s .. +590s
        kept = _filter_heartbeats_by_windows(beats, windows)
        kept_secs = sum(d for _, d in kept)
        # 窗口 [−45,+45] ∪ [+477,+567]：交集内心跳 = 9 + 9 = 18 条
        assert len(kept) == 18
        assert kept_secs == 180.0

    def test_empty_events_no_study_time(self):
        beats = _beats(T0, 60)
        assert _filter_heartbeats_by_windows(beats, []) == []

    def test_unsorted_heartbeats_safe(self):
        # 调用方保证升序；乱序是契约外输入，至少不能崩溃
        windows = _build_study_windows([T0])
        beats = [(T0 + timedelta(seconds=20), 10.0), (T0 - timedelta(seconds=20), 10.0)]
        kept = _filter_heartbeats_by_windows(beats, windows)
        assert isinstance(kept, list)


class TestColorForMinutes:
    def test_gray_for_zero(self):
        assert color_for_minutes(0) == "#ebedf0"

    def test_light_green_low(self):
        assert color_for_minutes(1) == "#9be9a8"
        assert color_for_minutes(15) == "#9be9a8"

    def test_mid_green(self):
        assert color_for_minutes(16) == "#40c463"
        assert color_for_minutes(30) == "#40c463"

    def test_dark_green(self):
        assert color_for_minutes(31) == "#30a14e"
        assert color_for_minutes(45) == "#30a14e"

    def test_deepest_green(self):
        assert color_for_minutes(46) == "#216e39"
        assert color_for_minutes(120) == "#216e39"
        assert color_for_minutes(9999) == "#216e39"

    def test_all_levels_covered(self):
        for lo, hi, color in COLOR_LEVELS:
            for m in [lo, (lo + hi) // 2, hi]:
                assert color_for_minutes(m) == color


class TestCategorizeReviewMode:
    def test_spelling_modes(self):
        for m in ["word-recall", "word-hinted", "word-preview", "word-context"]:
            assert categorize_review_mode(m) == "spelling", f"failed: {m}"

    def test_english_to_chinese_modes(self):
        for m in ["word-english_to_chinese", "word-listen_choose_chinese", "word-match_translation"]:
            assert categorize_review_mode(m) == "english_to_chinese", f"failed: {m}"

    def test_chinese_to_english_modes(self):
        for m in ["word-chinese_to_english", "word-listen_spell", "word-missing_letter", "word-hidden_recall"]:
            assert categorize_review_mode(m) == "chinese_to_english", f"failed: {m}"

    def test_phrase(self):
        assert categorize_review_mode("phrase-review") == "phrase"

    def test_sentence(self):
        assert categorize_review_mode("sentence-spelling") == "sentence"
        assert categorize_review_mode("sentence-cloze") == "sentence"

    def test_none_or_unknown(self):
        assert categorize_review_mode(None) == "other"
        assert categorize_review_mode("") == "other"
        assert categorize_review_mode("weird-mode") == "other"


class TestDateParsing:
    def test_valid_date(self):
        from app.utils import parse_date_param
        d = parse_date_param("2026-06-11")
        assert d == date(2026, 6, 11)

    def test_invalid_date(self):
        from app.utils import parse_date_param
        assert parse_date_param("not-a-date") is None
        assert parse_date_param("") is None
        assert parse_date_param("2026/06/11") is None
