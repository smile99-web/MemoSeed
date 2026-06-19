"""
Pytest fixtures for FSRS application tests.

The test suite uses a mocked DB session (`MagicMock`) rather than a real
Postgres connection. This is intentional:

1. The scheduler's scheduling decisions are pure-Python functions that
   only need the `user_model_settings` row to know which weights to use.
2. Avoiding a real DB means the tests run in <1s on any platform (Windows,
   macOS, Linux) without needing Docker or Postgres installed.
3. The trade-off is that schema-level changes (e.g. adding a new column)
   won't be caught by these tests. The production integration test
   (running on a real DB) is out of scope for the FSRS verification Goal.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

# Make `app.*` importable when running pytest from any directory.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


@pytest.fixture
def mock_db():
    """A MagicMock DB session. Tests configure `.scalar(...)` per-call."""
    return MagicMock()


@pytest.fixture
def user_id():
    """A stable UUID for tests."""
    return UUID("00000000-0000-0000-0000-000000000001")


def make_settings(*, fsrs_weights=None, use_slow_learner=False, use_child_profile=True) -> SimpleNamespace:
    """Build a UserModelSettings-shaped mock with the given fields."""
    settings: dict = {}
    if fsrs_weights is not None:
        settings["fsrsWeights"] = list(fsrs_weights)
    settings["useChildProfile"] = use_child_profile
    settings["useSlowLearnerProfile"] = use_slow_learner
    return SimpleNamespace(settings=settings)


def make_learning_item(*, item_type: str = "word", english_text: str = "apple") -> SimpleNamespace:
    """Build a LearningItem-shaped mock for `adjust_delay_for_learning_item`."""
    return SimpleNamespace(
        item_type=item_type,
        english_text=english_text,
        difficulty_level=3,
    )


def make_memory_state(**overrides) -> SimpleNamespace:
    """Build a MemoryState-shaped mock with sane FSRS defaults."""
    base = dict(
        interval_days=0,
        ease_factor=5.0,
        memory_strength=0.0,
        forget_risk=1.0,
        repetition_count=0,
        lapse_count=0,
        consecutive_correct_count=0,
        consecutive_error_count=0,
        recall_correct_count=0,
        hinted_correct_count=0,
        preview_correct_count=0,
        context_correct_count=0,
        last_reviewed_at=None,
        next_review_at=None,
        short_term_stability=1.0,
        last_short_term_updated_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)
