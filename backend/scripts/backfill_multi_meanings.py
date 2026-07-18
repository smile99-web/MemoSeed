"""
M3 backfill: multi-meaning Chinese translations.

Run ONCE after deploying the polysemy backend changes (M1/M2 dictionary
merge+expansion, M5 sanitizer/prompt). Three steps, all idempotent:

1. Refresh word-type learning_items.chinese_text from the improved built-in
   dictionary (words the dictionary covers get the curated multi-meaning
   value, e.g. like -> 喜欢；像 instead of a single or stale meaning).
2. Refresh word_translations rows for dictionary-covered words — the
   dictionary wins over stale cached values (including LLM-sourced ones like
   like -> 比如). Non-dictionary words keep their LLM translations.
3. Merge duplicate word learning_items (same user + same normalized word):
   keep the row linked from word_memory_states (else the one with the most
   review_logs, else the oldest), re-point every FK table
   (review_logs/mistake_logs/learning_events/points_logs/word_review_tasks/
   word_memory_states/memory_states) to the keeper, then delete the
   duplicate. memory_states has a UNIQUE constraint on learning_item_id, so
   the duplicate's row is merged (kept) or dropped instead of re-pointed.

Usage
-----
    # from the backend root on the VPS (venv active)
    python scripts/backfill_multi_meanings.py --dry-run   # report only
    python scripts/backfill_multi_meanings.py --apply     # write changes
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

# --- Cross-platform path handling ---------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.learning_event import LearningEvent  # noqa: E402
from app.models.learning_item import LearningItem  # noqa: E402
from app.models.memory_state import MemoryState  # noqa: E402
from app.models.mistake_log import MistakeLog  # noqa: E402
from app.models.review_log import ReviewLog  # noqa: E402
from app.models.user_points import PointsLog  # noqa: E402
from app.models.word_memory_state import WordMemoryState  # noqa: E402
from app.models.word_review_task import WordReviewTask  # noqa: E402
from app.models.word_translation import WordTranslation  # noqa: E402
from app.services.word_dictionary import BUILTIN_WORD_DICTIONARY  # noqa: E402


def norm(value: str | None) -> str:
    return (value or "").strip().lower()


def refresh_learning_items(db, dry_run: bool) -> tuple[int, int]:
    """Step 1: dictionary-refresh word-type learning_items. Returns (scanned, updated)."""
    items = db.scalars(select(LearningItem).where(LearningItem.item_type == "word")).all()
    updated = 0
    for item in items:
        key = norm(item.english_text)
        dict_value = BUILTIN_WORD_DICTIONARY.get(key)
        if not dict_value:
            continue
        if (item.chinese_text or "").strip() != dict_value:
            updated += 1
            if not dry_run:
                item.chinese_text = dict_value
                db.add(item)
    return len(items), updated


def refresh_word_translations(db, dry_run: bool) -> tuple[int, int]:
    """Step 2: dictionary wins for cached translations of covered words. Returns (scanned, updated)."""
    rows = db.scalars(select(WordTranslation)).all()
    updated = 0
    for row in rows:
        key = norm(row.word)
        dict_value = BUILTIN_WORD_DICTIONARY.get(key)
        if not dict_value:
            continue
        if (row.chinese_text or "").strip() != dict_value or row.source != "dictionary":
            updated += 1
            if not dry_run:
                row.chinese_text = dict_value
                row.source = "dictionary"
                db.add(row)
    return len(rows), updated


def merge_duplicate_word_items(db, dry_run: bool) -> tuple[int, int, int]:
    """Step 3: merge duplicate word learning_items. Returns (groups, merged_items, repointed_rows)."""
    items = db.scalars(select(LearningItem).where(LearningItem.item_type == "word")).all()
    groups: dict[tuple[str, str], list[LearningItem]] = defaultdict(list)
    for item in items:
        groups[(str(item.user_id), norm(item.english_text))].append(item)

    dup_groups = {key: rows for key, rows in groups.items() if len(rows) > 1 and key[1]}
    merged_items = 0
    repointed_rows = 0

    for (_user_id, word), rows in sorted(dup_groups.items()):
        # Keeper preference: linked from word_memory_states, then most review_logs, then oldest.
        def has_word_state(item) -> bool:
            return db.scalar(
                select(func.count()).select_from(WordMemoryState).where(WordMemoryState.learning_item_id == item.id)
            ) > 0

        def review_count(item) -> int:
            return db.scalar(
                select(func.count()).select_from(ReviewLog).where(ReviewLog.learning_item_id == item.id)
            ) or 0

        rows.sort(key=lambda it: (not has_word_state(it), -review_count(it), it.created_at))
        keeper, dups = rows[0], rows[1:]
        print(f"  merge {word!r}: keep {keeper.id} (reviews={review_count(keeper)}), drop {[str(d.id) for d in dups]}")

        for dup in dups:
            merged_items += 1
            if dry_run:
                continue
            # Re-point plain FK tables.
            for model in (ReviewLog, MistakeLog, LearningEvent, PointsLog, WordReviewTask, WordMemoryState):
                result = db.execute(
                    model.__table__.update()
                    .where(model.learning_item_id == dup.id)
                    .values(learning_item_id=keeper.id)
                )
                repointed_rows += result.rowcount or 0
            # memory_states is UNIQUE on learning_item_id: re-point only if the
            # keeper has no row yet, otherwise drop the duplicate's row.
            keeper_state = db.scalar(select(MemoryState).where(MemoryState.learning_item_id == keeper.id))
            dup_state = db.scalar(select(MemoryState).where(MemoryState.learning_item_id == dup.id))
            if dup_state is not None:
                if keeper_state is None:
                    dup_state.learning_item_id = keeper.id
                    db.add(dup_state)
                    repointed_rows += 1
                else:
                    db.delete(dup_state)
            db.delete(dup)

    return len(dup_groups), merged_items, repointed_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default is dry-run)")
    args = parser.parse_args()
    dry_run = not args.apply

    db = SessionLocal()
    try:
        scanned_items, updated_items = refresh_learning_items(db, dry_run)
        print(f"[1] learning_items(word): scanned={scanned_items} refresh={'would ' if dry_run else ''}{updated_items}")

        scanned_tr, updated_tr = refresh_word_translations(db, dry_run)
        print(f"[2] word_translations: scanned={scanned_tr} refresh={'would ' if dry_run else ''}{updated_tr}")

        groups, merged, repointed = merge_duplicate_word_items(db, dry_run)
        print(f"[3] duplicate groups={groups} merged_items={'would ' if dry_run else ''}{merged} repointed_rows={repointed}")

        if dry_run:
            db.rollback()
            print("dry-run: no changes written")
        else:
            db.commit()
            print("committed")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
