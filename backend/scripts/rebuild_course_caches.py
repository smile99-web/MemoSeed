"""Rebuild speech/translation caches for the three course packages.

Replicates the logic of POST /courses/{id}/cache-rebuild, but runs
server-side over every course of the three packages.

Usage:
    python rebuild_course_caches.py --dry-run   # report missing counts only
    python rebuild_course_caches.py             # full rebuild
"""
import sys

sys.path.insert(0, "/opt/MemoSeed/backend")

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.course import Course  # noqa: E402
from app.models.course_package import CoursePackage  # noqa: E402
from app.models.learning_item import LearningItem  # noqa: E402
from app.models.speech_asset import SpeechAsset  # noqa: E402
from app.services.secure_model_settings import get_private_model_settings  # noqa: E402
from app.services.word_translation_cache import (  # noqa: E402
    ensure_word_translations,
    get_cached_word_translations,
    sanitize_word_translation,
)
from app.services.llm_translation import (  # noqa: E402
    needs_translation,
    translate_english_to_chinese,
)
from app.services.speech_asset_cache import (  # noqa: E402
    build_learning_speech_targets,
    ensure_volcengine_speech_asset,
)
from app.services.tts_cache import build_cache_key, get_cached_audio  # noqa: E402
from app.api.v1.learning.router import (  # noqa: E402
    build_llm_translation_settings,
    collect_course_terms,
)

PACKAGE_NAMES = ["中考英语", "常用单词200", "常用单词500"]
DRY_RUN = "--dry-run" in sys.argv


def main() -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "cmx@a.com"))
        if user is None:
            raise SystemExit("user cmx@a.com not found")
        stored_settings = get_private_model_settings(db, user.id)
        translation_settings = build_llm_translation_settings(None, None, None, None, stored_settings)

        courses = db.scalars(
            select(Course)
            .join(CoursePackage, Course.package_id == CoursePackage.id)
            .where(CoursePackage.name.in_(PACKAGE_NAMES), Course.user_id == user.id)
            .order_by(CoursePackage.name, Course.name)
        ).all()
        print(f"user={user.email} courses={len(courses)} dry_run={DRY_RUN}", flush=True)

        totals = {"sent_zh": 0, "terms": 0, "speech_cached": 0, "speech_missing": 0, "errors": 0}
        for course in courses:
            pkg = db.get(CoursePackage, course.package_id)
            items = db.scalars(
                select(LearningItem)
                .where(LearningItem.user_id == user.id, LearningItem.course_id == course.id)
                .order_by(LearningItem.sort_order.asc(), LearningItem.created_at.asc())
            ).all()
            if not items:
                print(f"[{pkg.name}/{course.name}] empty, skip", flush=True)
                continue
            stats = {"sent_zh": 0, "terms": 0, "speech_cached": 0, "speech_missing": 0, "errors": 0}

            # --- stage 1: sentence chinese translations ---
            need_zh = [it for it in items if needs_translation(it.chinese_text)]
            if DRY_RUN:
                stats["sent_zh"] = len(need_zh)
            else:
                for item in need_zh:
                    try:
                        if item.item_type == "word":
                            item.chinese_text = sanitize_word_translation(
                                translate_english_to_chinese(item.english_text, translation_settings, multiple_meanings=True),
                                source_word=item.english_text,
                            )
                            if not item.chinese_text:
                                raise ValueError("empty after sanitize")
                        else:
                            item.chinese_text = translate_english_to_chinese(item.english_text, translation_settings)
                        db.add(item)
                        stats["sent_zh"] += 1
                    except ValueError:
                        stats["errors"] += 1
                db.commit()

            # --- stage 2: term translations ---
            terms = collect_course_terms(items)
            cached_terms = get_cached_word_translations(db, user.id, terms)
            missing_terms = [t for t in terms if t not in cached_terms]
            if DRY_RUN:
                stats["terms"] = len(missing_terms)
            else:
                for term in missing_terms:
                    before = len(get_cached_word_translations(db, user.id, [term]))
                    translations = ensure_word_translations(db, user.id, [term], translation_settings, course.id)
                    db.commit()
                    if term in translations and before == 0:
                        stats["terms"] += 1
                    else:
                        stats["errors"] += 1

            # --- stage 3: speech assets ---
            targets = build_learning_speech_targets(db, user_id=user.id, learning_items=items, stored_settings=stored_settings)
            if DRY_RUN:
                hashes = [build_cache_key(t.text.strip(), t.voice, t.speech_rate) for t in targets if t.text.strip()]
                rows = db.scalars(
                    select(SpeechAsset).where(
                        SpeechAsset.user_id == user.id,
                        SpeechAsset.cached.is_(True),
                        SpeechAsset.text_hash.in_(hashes),
                    )
                ).all() if hashes else []
                cached_keys = {(r.language, r.voice, r.speech_rate, r.text_hash) for r in rows}
                missing = 0
                for t in targets:
                    key = (t.language, t.voice, t.speech_rate, build_cache_key(t.text.strip(), t.voice, t.speech_rate))
                    if key in cached_keys and get_cached_audio(t.text.strip(), t.voice, t.speech_rate) is not None:
                        stats["speech_cached"] += 1
                    else:
                        missing += 1
                stats["speech_missing"] = missing
            else:
                synthesis_failures = 0
                for target in targets:
                    asset, failed = ensure_volcengine_speech_asset(
                        db,
                        user_id=user.id,
                        course_id=course.id,
                        target=target,
                        stored_settings=stored_settings,
                        allow_synthesis=synthesis_failures < 3,
                    )
                    if failed:
                        synthesis_failures += 1
                        stats["errors"] += 1
                    if asset is not None and asset.cached:
                        stats["speech_cached"] += 1
                    else:
                        stats["speech_missing"] += 1
                db.commit()

            for k in totals:
                totals[k] += stats[k]
            print(
                f"[{pkg.name}/{course.name}] items={len(items)} sent_zh={stats['sent_zh']} "
                f"terms={stats['terms']} speech_ok={stats['speech_cached']} "
                f"speech_missing={stats['speech_missing']} errors={stats['errors']}",
                flush=True,
            )
        print(f"TOTAL {totals}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
