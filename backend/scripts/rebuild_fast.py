"""Fast parallel cache rebuild for the three course packages.

Two global phases over deduped work items instead of per-course sequential:
  1. missing term translations  -> 4 concurrent LLM calls (HTTP only),
     then sequential DB writes (no cross-thread session use).
  2. missing speech targets     -> 8 concurrent TTS calls (HTTP only,
     disjoint texts so no file/DB races), then sequential asset upserts.

Run AFTER rebuild_course_caches.py is stopped. Idempotent: already cached
assets are skipped.
"""
import sys

sys.path.insert(0, "/opt/MemoSeed/backend")

from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.course import Course  # noqa: E402
from app.models.course_package import CoursePackage  # noqa: E402
from app.models.learning_item import LearningItem  # noqa: E402
from app.models.word_translation import WordTranslation  # noqa: E402
from app.services.secure_model_settings import get_private_model_settings  # noqa: E402
from app.services.word_translation_cache import (  # noqa: E402
    get_cached_word_translations,
    sanitize_word_translation,
)
from app.services.llm_translation import translate_english_to_chinese  # noqa: E402
from app.services.speech_asset_cache import (  # noqa: E402
    build_learning_speech_targets,
    build_volcengine_tts_settings,
    upsert_speech_asset,
)
from app.services.volcengine_tts import AUDIO_SUFFIX, synthesize_volcengine_speech  # noqa: E402
from app.services.tts_cache import get_cached_audio  # noqa: E402
from app.api.v1.learning.router import (  # noqa: E402
    build_llm_translation_settings,
    collect_course_terms,
)
from app.utils import normalize_word  # noqa: E402

PACKAGE_NAMES = ["中考英语", "常用单词200", "常用单词500"]


def main() -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "cmx@a.com"))
        if user is None:
            raise SystemExit("user not found")
        stored_settings = get_private_model_settings(db, user.id)
        translation_settings = build_llm_translation_settings(None, None, None, None, stored_settings)

        courses = db.scalars(
            select(Course)
            .join(CoursePackage, Course.package_id == CoursePackage.id)
            .where(CoursePackage.name.in_(PACKAGE_NAMES), Course.user_id == user.id)
        ).all()
        course_items: dict = {}
        for course in courses:
            items = db.scalars(
                select(LearningItem)
                .where(LearningItem.user_id == user.id, LearningItem.course_id == course.id)
                .order_by(LearningItem.sort_order.asc(), LearningItem.created_at.asc())
            ).all()
            if items:
                course_items[course.id] = items
        print(f"courses with items: {len(course_items)}", flush=True)

        # ---- phase 1: term translations ----
        all_terms: set[str] = set()
        for items in course_items.values():
            all_terms.update(collect_course_terms(items))
        cached_terms = get_cached_word_translations(db, user.id, sorted(all_terms))
        missing_terms = [t for t in sorted(all_terms) if t not in cached_terms]
        print(f"terms total={len(all_terms)} missing={len(missing_terms)}", flush=True)

        def translate_term(term: str):
            try:
                text = sanitize_word_translation(
                    translate_english_to_chinese(term, translation_settings, multiple_meanings=True),
                    source_word=term,
                )
                return term, text or None
            except Exception as exc:  # noqa: BLE001
                print(f"  term failed: {term}: {exc}", flush=True)
                return term, None

        translated: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(translate_term, term) for term in missing_terms]
            for done, future in enumerate(as_completed(futures), start=1):
                term, text = future.result()
                if text:
                    translated[term] = text
                if done % 50 == 0:
                    print(f"  terms {done}/{len(missing_terms)}", flush=True)

        written = 0
        for term, text in translated.items():
            normalized = normalize_word(term) or term
            row = db.scalar(
                select(WordTranslation).where(
                    WordTranslation.user_id == user.id,
                    WordTranslation.word == normalized,
                )
            )
            if row is None:
                db.add(WordTranslation(user_id=user.id, course_id=None, word=normalized, chinese_text=text, source="llm"))
            else:
                row.chinese_text = text
                row.source = "llm"
            written += 1
        db.commit()
        print(f"terms translated={written} failed={len(missing_terms) - written}", flush=True)

        # ---- phase 2: speech targets ----
        target_map: dict = {}
        for course_id, items in course_items.items():
            for target in build_learning_speech_targets(db, user_id=user.id, learning_items=items, stored_settings=stored_settings):
                key = (target.text, target.language, target.voice, target.speech_rate)
                target_map.setdefault(key, (target, course_id))
        targets = [value[0] for value in target_map.values()]
        missing_targets = [
            t for t in targets
            if get_cached_audio(t.text, t.voice, t.speech_rate, suffix=AUDIO_SUFFIX) is None
        ]
        print(f"speech targets total={len(targets)} missing={len(missing_targets)}", flush=True)

        def synth(target):
            settings = build_volcengine_tts_settings(
                stored_settings,
                voice=target.voice,
                language=target.language,
                speech_rate=target.speech_rate,
            )
            if not settings.api_key:
                return target, False
            try:
                synthesize_volcengine_speech(target.text, settings)
                return target, True
            except Exception as exc:  # noqa: BLE001
                print(f"  tts failed: {target.text[:40]}: {exc}", flush=True)
                return target, False

        failed_targets = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(synth, target) for target in missing_targets]
            for done, future in enumerate(as_completed(futures), start=1):
                target, ok = future.result()
                if not ok:
                    failed_targets.append(target)
                if done % 200 == 0:
                    print(f"  speech {done}/{len(missing_targets)}", flush=True)

        # one sequential retry pass for transient failures
        if failed_targets:
            print(f"retrying {len(failed_targets)} failed targets sequentially", flush=True)
            still_failed = []
            for target in failed_targets:
                _, ok = synth(target)
                if not ok:
                    still_failed.append(target)
            failed_targets = still_failed

        # upsert asset rows for every target (links course, records cached flag)
        cached_count = 0
        for key, (target, course_id) in target_map.items():
            is_cached = get_cached_audio(target.text, target.voice, target.speech_rate, suffix=AUDIO_SUFFIX) is not None
            upsert_speech_asset(db, user_id=user.id, course_id=course_id, target=target, cached=is_cached)
            if is_cached:
                cached_count += 1
        db.commit()
        print(f"speech assets cached={cached_count}/{len(targets)} still_missing={len(failed_targets)}", flush=True)
        print("DONE", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
