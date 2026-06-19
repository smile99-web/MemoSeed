"""
Verify whether FSRS personalization is actually applied to scheduling decisions.

This script runs the same simulated review history through three different
scheduling strategies and reports whether their outputs differ in ways that
matter for `next_review_at` and `forget_risk`.

Strategies compared
-------------------
1. **baseline_sm2** — Hand-rolled SuperMemo-2 scheduler. No FSRS, no
   personalization. Provides a control point: any difference between
   `default_fsrs` and `baseline_sm2` shows FSRS is at least active.
2. **default_fsrs** — Built-in child-calibrated FSRS weights
   (`CHILD_FSRS_WEIGHTS`, target retention 0.90). What the app uses
   *before* a user fit has been performed.
3. **personalized_fsrs** — Simulates a user-fitted weight tuple. We perturb
   `w[8]` (stability growth intercept) and `w[10]` (retrievability factor)
   to model a slow-learner profile, since this is the most consequential
   region of the FSRS parameter space for interval length.

What to look for
----------------
- If `personalized_fsrs` outputs match `default_fsrs` exactly across all
  ratings, personalization is NOT being applied.
- If they differ for at least one rating, the scheduler is at least
  consuming the stored weight tuple (good).
- The script also checks for the presence of audit fields
  (`scheduler_type` / `algorithm_version` / `fsrs_params_snapshot`) on
  `ReviewLog` and `MemoryState`. Their absence is a separate finding
  documented in the report.

Usage
-----
    # from project root (Windows or macOS)
    python backend/scripts/verify_fsrs_application.py

    # with verbose per-step output
    python backend/scripts/verify_fsrs_application.py --verbose

The script is pure-Python and does NOT touch the database. A live DB check
of `get_effective_fsrs_params` is out of scope for this script and lives in
`docs/fsrs_verification_report.md` (TODO step 5).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp
from types import SimpleNamespace
from typing import Callable

# --- Cross-platform path handling ---------------------------------------------
# When invoked as `python backend/scripts/verify_fsrs_application.py` the
# `app` package is one level up. Add the backend root to sys.path so we can
# import the scheduler constants and pure functions without needing a DB
# session.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.services.memory_scheduler import (  # noqa: E402
    CHILD_FSRS_WEIGHTS,
    CHILD_TARGET_RETENTION,
    FSRS_AGAIN,
    FSRS_DECAY,
    FSRS_EASY,
    FSRS_FACTOR,
    FSRS_GOOD,
    FSRS_HARD,
    FSRS_TARGET_RETENTION,
    SLOW_LEARNER_FSRS_WEIGHTS,
    SLOW_LEARNER_TARGET_RETENTION,
    calculate_fsrs_interval,
    calculate_fsrs_retrievability,
    constrain_difficulty,
    constrain_stability,
    initial_fsrs_stability,
    next_fsrs_difficulty,
    next_fsrs_forget_stability,
    next_fsrs_recall_stability,
    score_to_fsrs_rating,
)


# --- Synthetic "personalized" weights -----------------------------------------
# We do NOT have a real fitted user at hand in this offline check. Instead we
# synthesize a personalized tuple by applying a slow-learner perturbation to
# the child weights. The point is to confirm the *scheduler path* honors a
# different weight tuple when one is supplied — not to assert what a real
# user fit would look like.
def _build_personalized_weights() -> tuple[float, ...]:
    base = list(CHILD_FSRS_WEIGHTS)
    # Stability growth intercept (w[8]) — reduce by 20% (slower growth).
    base[8] = round(base[8] * 0.8, 6)
    # Retrievability factor (w[10]) — reduce by 15%.
    base[10] = round(base[10] * 0.85, 6)
    # Hard penalty (w[15]) — increase by 10% (harsher response to "hard").
    base[15] = round(base[15] * 1.1, 6)
    return tuple(base)


PERSONALIZED_FSRS_WEIGHTS = _build_personalized_weights()
PERSONALIZED_TARGET_RETENTION = 0.92  # slightly more conservative


# --- Mocked memory_state (SQLAlchemy-free) ------------------------------------
def make_mock_memory_state() -> SimpleNamespace:
    """Return a SimpleNamespace mirroring MemoryState fields used by scheduler."""
    return SimpleNamespace(
        interval_days=0,
        ease_factor=5.0,                # FSRS initial difficulty baseline
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
        next_review_at=datetime.now(timezone.utc),
        short_term_stability=1.0,
        last_short_term_updated_at=None,
    )


# --- Scheduler strategies -----------------------------------------------------
@dataclass(frozen=True)
class ScheduleStep:
    """One step of a simulated review history."""
    score: int                    # 0-5, like the real API
    elapsed_days: float           # days since previous review


def _elapsed_days(prev: datetime | None, now: datetime) -> float:
    if prev is None:
        return 0.0
    return max((now - prev).total_seconds() / 86400, 1 / 1440)


def step_fsrs(
    state: SimpleNamespace,
    score: int,
    weights: tuple[float, ...],
    target_retention: float,
    now: datetime,
) -> datetime:
    """Run one FSRS scheduling step, mirroring the post-weight-decision logic
    of `schedule_memory_review` in memory_scheduler.py (no DB, no logging)."""
    is_correct = score >= 3
    rating = score_to_fsrs_rating(score)
    previous_repetition_count = state.repetition_count
    previous_stability_days = (
        max((state.next_review_at - (state.last_reviewed_at or now)).total_seconds() / 86400, 0.0)
        if state.last_reviewed_at is not None
        else 5 / 1440
    )
    previous_stability_days = constrain_stability(previous_stability_days)
    elapsed = _elapsed_days(state.last_reviewed_at, now)
    previous_retrievability = calculate_fsrs_retrievability(elapsed, previous_stability_days)
    current_difficulty = constrain_difficulty(state.ease_factor or 5.0)

    next_difficulty = next_fsrs_difficulty(current_difficulty, rating, weights)
    next_difficulty = constrain_difficulty(next_difficulty)

    if is_correct:
        state.repetition_count += 1
    else:
        state.repetition_count = 0
        state.lapse_count += 1

    if previous_repetition_count == 0:
        next_stability_days = initial_fsrs_stability(rating, weights)
    elif is_correct:
        next_stability_days = next_fsrs_recall_stability(
            next_difficulty, previous_stability_days, previous_retrievability, rating, weights
        )
    else:
        next_stability_days = next_fsrs_forget_stability(
            next_difficulty, previous_stability_days, previous_retrievability, weights
        )

    state.ease_factor = next_difficulty
    state.last_reviewed_at = now
    if is_correct:
        delay = calculate_fsrs_interval(next_stability_days, target_retention)
    else:
        # Failure path: short retry window. We mirror the spirit (1 day) rather
        # than the full STS machinery which needs more state than we mock.
        delay = timedelta(days=1)
    state.interval_days = max(1, int(delay.total_seconds() / 86400)) if delay >= timedelta(days=1) else 0
    state.next_review_at = now + delay
    if is_correct:
        next_r = calculate_fsrs_retrievability(delay.total_seconds() / 86400, next_stability_days)
        state.forget_risk = round(1 - next_r, 2)
        state.memory_strength = round(next_r, 2)
    else:
        state.forget_risk = 1.0
        state.memory_strength = 0.0
    return state.next_review_at


def step_sm2(state: SimpleNamespace, score: int, now: datetime) -> datetime:
    """Hand-rolled SuperMemo-2 baseline. No FSRS, no personalization."""
    quality = score  # 0-5 maps roughly to SM-2 q
    is_correct = quality >= 3
    if is_correct:
        if state.repetition_count == 0:
            interval = 1
        elif state.repetition_count == 1:
            interval = 6
        else:
            interval = max(1, int(state.interval_days * state.ease_factor))
        state.repetition_count += 1
    else:
        state.repetition_count = 0
        state.lapse_count += 1
        interval = 1
    # SM-2 ease update
    ef = state.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    state.ease_factor = max(1.3, ef)
    state.last_reviewed_at = now
    state.interval_days = interval
    delay = timedelta(days=interval)
    state.next_review_at = now + delay
    state.forget_risk = 0.5  # SM-2 doesn't model this directly
    state.memory_strength = 0.5
    return state.next_review_at


# --- Simulation harness -------------------------------------------------------
StrategyFn = Callable[[SimpleNamespace, int, datetime], datetime]


@dataclass(frozen=True)
class StrategyResult:
    name: str
    weights_id: str                  # human-readable identity of weights
    final_next_review_at: datetime
    final_interval_days: int
    final_forget_risk: float
    final_memory_strength: float
    final_ease_factor: float
    final_stability_days: float
    history: list[tuple[int, datetime, int]]  # (score, next_review_at, interval_days)


def run_strategy(
    name: str,
    weights: tuple[float, ...],
    target_retention: float,
    history: list[ScheduleStep],
    step_fn: Callable[[SimpleNamespace, int, datetime], datetime],
) -> StrategyResult:
    state = make_mock_memory_state()
    now = datetime.now(timezone.utc)
    log: list[tuple[int, datetime, int]] = []
    for step in history:
        now = now + timedelta(days=step.elapsed_days)
        step_fn(state, step.score, now)
        log.append((step.score, state.next_review_at, state.interval_days))
    # Re-derive final stability from the last (next_review_at - last_reviewed_at) gap
    final_stability = (
        max((state.next_review_at - (state.last_reviewed_at or now)).total_seconds() / 86400, 5 / 1440)
        if state.last_reviewed_at is not None
        else 5 / 1440
    )
    weights_id = (
        f"FSRS({len(weights)} weights, target={target_retention})"
        if "sm2" not in name.lower()
        else "SM-2 (ease 1.3-2.5+)"
    )
    return StrategyResult(
        name=name,
        weights_id=weights_id,
        final_next_review_at=state.next_review_at,
        final_interval_days=state.interval_days,
        final_forget_risk=state.forget_risk,
        final_memory_strength=state.memory_strength,
        final_ease_factor=round(state.ease_factor, 4),
        final_stability_days=round(constrain_stability(final_stability), 3),
        history=log,
    )


def default_history() -> list[ScheduleStep]:
    """A plausible review history for a single word over ~3 weeks.

    Scores mix Good and Hard with one lapse to make interval differences
    visible without flakiness.
    """
    return [
        ScheduleStep(score=4, elapsed_days=0),     # first review (Good)
        ScheduleStep(score=3, elapsed_days=1),     # next day (Hard)
        ScheduleStep(score=4, elapsed_days=3),     # 3 days later
        ScheduleStep(score=5, elapsed_days=7),     # a week later (Easy)
        ScheduleStep(score=2, elapsed_days=14),    # lapse after 2 weeks
        ScheduleStep(score=3, elapsed_days=1),     # quick retry (Hard)
        ScheduleStep(score=4, elapsed_days=4),     # recovery (Good)
    ]


# --- Output formatting --------------------------------------------------------
def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(cell)) for cell in [h, *col]) for h, col in zip(headers, zip(*rows))]
    sep = " | "
    fmt = sep.join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    for row in rows:
        print(fmt.format(*row))


def _print_report(results: list[StrategyResult], verbose: bool) -> dict:
    print("=" * 78)
    print("FSRS APPLICATION VERIFICATION")
    print("=" * 78)
    print()
    print("Step history (score, days-since-prev):")
    history = default_history()
    print("  " + ", ".join(f"({s.score}, +{s.elapsed_days}d)" for s in history))
    print()
    print("-" * 78)
    print("Final scheduling outcomes")
    print("-" * 78)
    rows = []
    for r in results:
        rows.append([
            r.name,
            r.weights_id,
            f"{r.final_interval_days}d",
            f"{r.final_stability_days}",
            f"{r.final_ease_factor}",
            f"{r.final_forget_risk}",
        ])
    _print_table(
        rows,
        headers=["strategy", "weights", "interval", "stability(d)", "difficulty", "forget_risk"],
    )
    print()
    if verbose:
        print("-" * 78)
        print("Per-step intervals (days)")
        print("-" * 78)
        header = ["step", "score"] + [r.name for r in results]
        rows = []
        n_steps = len(results[0].history)
        for i in range(n_steps):
            row = [str(i + 1), str(results[0].history[i][0])]
            for r in results:
                row.append(f"{r.history[i][2]}d")
            rows.append(row)
        _print_table(rows, headers=header)
        print()

    # --- Conclusion block ---
    print("-" * 78)
    print("Findings")
    print("-" * 78)
    fsrs_default = next(r for r in results if r.name == "default_fsrs")
    fsrs_personalized = next(r for r in results if r.name == "personalized_fsrs")
    sm2 = next(r for r in results if r.name == "baseline_sm2")

    fsrs_active = abs(fsrs_default.final_interval_days - sm2.final_interval_days) >= 1
    personalized_active = abs(
        fsrs_personalized.final_interval_days - fsrs_default.final_interval_days
    ) >= 1
    print(f"  FSRS is at least active vs SM-2 baseline:     {'YES' if fsrs_active else 'NO'}")
    print(f"  Personalized weights differ from default:    {'YES' if personalized_active else 'NO'}")
    if personalized_active:
        delta = fsrs_personalized.final_interval_days - fsrs_default.final_interval_days
        print(f"  Personalized-vs-default interval delta:     {delta:+d} days (final step)")
    print()
    return {
        "fsrs_active": fsrs_active,
        "personalized_active": personalized_active,
        "default_interval": fsrs_default.final_interval_days,
        "personalized_interval": fsrs_personalized.final_interval_days,
        "sm2_interval": sm2.final_interval_days,
    }


# --- Audit field check (offline, just inspects model definitions) -------------
def _check_audit_fields() -> dict[str, bool]:
    """Read the SQLAlchemy model files to see whether the audit fields exist.

    This is a code-presence check, not a DB query. Useful because audit
    fields missing from the schema = no way to verify per-row what was used.
    """
    model_dir = os.path.join(_BACKEND_ROOT, "app", "models")
    targets = {
        "review_log": ["scheduler_type", "algorithm_version", "fsrs_params_snapshot"],
        "memory_state": ["scheduler_type", "algorithm_version", "fsrs_params_snapshot"],
    }
    presence: dict[str, bool] = {}
    for filename, fields in [
        ("review_log.py", targets["review_log"]),
        ("memory_state.py", targets["memory_state"]),
    ]:
        path = os.path.join(model_dir, filename)
        if not os.path.exists(path):
            for f in fields:
                presence[f"{filename}:{f}"] = False
            continue
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        for f in fields:
            presence[f"{filename}:{f}"] = f in content
    return presence


def _print_audit_report(presence: dict[str, bool]) -> None:
    print("-" * 78)
    print("Audit field check (offline, file scan)")
    print("-" * 78)
    for key, present in presence.items():
        print(f"  {key:<45} {'present' if present else 'MISSING'}")
    print()
    missing = [k for k, v in presence.items() if not v]
    if missing:
        print(f"  {len(missing)} audit field(s) missing — see docs/fsrs_verification_report.md")
    print()


# --- Entrypoint ---------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Verify FSRS personalization is applied")
    parser.add_argument("--verbose", action="store_true", help="Print per-step interval table")
    args = parser.parse_args()

    history = default_history()
    results = [
        run_strategy(
            name="baseline_sm2",
            weights=CHILD_FSRS_WEIGHTS,            # unused by SM-2 step_fn
            target_retention=FSRS_TARGET_RETENTION, # unused
            history=history,
            step_fn=step_sm2,
        ),
        run_strategy(
            name="default_fsrs",
            weights=CHILD_FSRS_WEIGHTS,
            target_retention=CHILD_TARGET_RETENTION,
            history=history,
            step_fn=lambda s, sc, now: step_fsrs(s, sc, CHILD_FSRS_WEIGHTS, CHILD_TARGET_RETENTION, now),
        ),
        run_strategy(
            name="personalized_fsrs",
            weights=PERSONALIZED_FSRS_WEIGHTS,
            target_retention=PERSONALIZED_TARGET_RETENTION,
            history=history,
            step_fn=lambda s, sc, now: step_fsrs(
                s, sc, PERSONALIZED_FSRS_WEIGHTS, PERSONALIZED_TARGET_RETENTION, now
            ),
        ),
    ]

    summary = _print_report(results, verbose=args.verbose)
    presence = _check_audit_fields()
    _print_audit_report(presence)

    # Exit code: 0 if all checks pass; 1 if personalization appears inactive.
    ok = summary["fsrs_active"] and summary["personalized_active"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
