from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import engine
from app.models.course_completion_log import CourseCompletionLog
from app.models.listening_story import ListeningStory
from app.models.speech_asset import SpeechAsset
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.models.word_translation import WordTranslation

# Route app-level loggers (e.g. the per-request pronunciation-check details)
# to stderr at INFO so systemd/journald captures them. uvicorn configures its
# own loggers; this only gives the root handler a level high enough that
# logger.info() lines survive instead of being silently dropped.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

logger = logging.getLogger(__name__)


def ensure_lightweight_schema_upgrades() -> None:
    statements = [
        "ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS consecutive_correct_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS consecutive_error_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS recall_correct_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS hinted_correct_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS preview_correct_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE memory_states ADD COLUMN IF NOT EXISTS context_correct_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE review_logs ADD COLUMN IF NOT EXISTS error_type VARCHAR(32)",
        "ALTER TABLE mistake_logs ADD COLUMN IF NOT EXISTS error_type VARCHAR(32)",
        # Learning replay: merge duplicate minute-stat rows (same
        # user/date/hour/minute) into the surviving row, summing the counters,
        # so the unique index below can be created safely.
        """DO $$
        BEGIN
            IF to_regclass('learning_minute_stats') IS NULL THEN
                RETURN;
            END IF;
            UPDATE learning_minute_stats s SET
                total_events = agg.total_events,
                spelling_events = agg.spelling_events,
                english_to_chinese_events = agg.english_to_chinese_events,
                chinese_to_english_events = agg.chinese_to_english_events,
                phrase_events = agg.phrase_events,
                sentence_events = agg.sentence_events,
                correct_events = agg.correct_events,
                incorrect_events = agg.incorrect_events,
                study_duration_ms = agg.study_duration_ms
            FROM (
                SELECT user_id, stat_date, stat_hour, stat_minute,
                       sum(total_events) AS total_events,
                       sum(spelling_events) AS spelling_events,
                       sum(english_to_chinese_events) AS english_to_chinese_events,
                       sum(chinese_to_english_events) AS chinese_to_english_events,
                       sum(phrase_events) AS phrase_events,
                       sum(sentence_events) AS sentence_events,
                       sum(correct_events) AS correct_events,
                       sum(incorrect_events) AS incorrect_events,
                       sum(study_duration_ms) AS study_duration_ms
                FROM learning_minute_stats
                GROUP BY user_id, stat_date, stat_hour, stat_minute
                HAVING count(*) > 1
            ) agg
            WHERE s.user_id = agg.user_id AND s.stat_date = agg.stat_date
              AND s.stat_hour = agg.stat_hour AND s.stat_minute = agg.stat_minute
              AND s.ctid = (
                  -- ctid, not min(id): the id column is uuid and PostgreSQL
                  -- has no min(uuid) aggregate.
                  SELECT min(x.ctid) FROM learning_minute_stats x
                  WHERE x.user_id = agg.user_id AND x.stat_date = agg.stat_date
                    AND x.stat_hour = agg.stat_hour AND x.stat_minute = agg.stat_minute
              );
            DELETE FROM learning_minute_stats a
            WHERE EXISTS (
                SELECT 1 FROM learning_minute_stats b
                WHERE b.user_id = a.user_id AND b.stat_date = a.stat_date
                  AND b.stat_hour = a.stat_hour AND b.stat_minute = a.stat_minute
                  AND b.ctid < a.ctid
            );
        END $$;""",
        # Learning replay: drop duplicate events per review_log (keep the
        # earliest), then enforce uniqueness so concurrent backfills and
        # double submissions can never double-count again.
        """DO $$
        BEGIN
            IF to_regclass('learning_events') IS NULL THEN
                RETURN;
            END IF;
            DELETE FROM learning_events a
            WHERE a.review_log_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM learning_events b
                WHERE b.review_log_id = a.review_log_id AND b.id < a.id
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_events_review_log_id
                ON learning_events (review_log_id) WHERE review_log_id IS NOT NULL;
        END $$;""",
        """DO $$
        BEGIN
            IF to_regclass('learning_minute_stats') IS NOT NULL THEN
                CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_minute_stats_user_minute
                    ON learning_minute_stats (user_id, stat_date, stat_hour, stat_minute);
            END IF;
        END $$;""",
    ]
    # Each statement in its own transaction: one failure (e.g. table missing
    # on a fresh install) must not block the remaining upgrades or startup.
    for statement in statements:
        try:
            with engine.begin() as connection:
                connection.execute(text(statement))
        except Exception as exc:
            logger.warning("Schema upgrade statement failed (will retry on next boot): %s", exc)


def warn_on_schema_drift() -> None:
    """Scream when a model column is missing from the live DB.

    The six tables below are created with create(checkfirst=True) and have
    NO column-evolution path (no Alembic versions exist — 2026-08-16 audit):
    a newly added model column silently never reaches prod, and the first
    request selecting/inserting it 500s with UndefinedColumn. Detect drift
    at startup so the deploy log shows the real problem instead of a
    per-request 500 days later.
    """
    from sqlalchemy import inspect as sa_inspect

    checkfirst_models = (CourseCompletionLog, SpeechAsset, WordMemoryState, WordReviewTask, WordTranslation, ListeningStory)
    try:
        with engine.connect() as connection:
            inspector = sa_inspect(connection)
            for model in checkfirst_models:
                table = model.__table__
                if not inspector.has_table(table.name):
                    continue  # fresh install — create(checkfirst) covers it
                db_columns = {column["name"] for column in inspector.get_columns(table.name)}
                missing = [column.name for column in table.columns if column.name not in db_columns]
                if missing:
                    logger.critical(
                        "Schema drift on %s: model columns missing in DB: %s "
                        "— add matching ALTERs to ensure_lightweight_schema_upgrades()",
                        table.name,
                        ", ".join(missing),
                    )
    except Exception:
        logger.exception("Schema drift check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    CourseCompletionLog.__table__.create(bind=engine, checkfirst=True)
    SpeechAsset.__table__.create(bind=engine, checkfirst=True)
    WordMemoryState.__table__.create(bind=engine, checkfirst=True)
    WordReviewTask.__table__.create(bind=engine, checkfirst=True)
    WordTranslation.__table__.create(bind=engine, checkfirst=True)
    ListeningStory.__table__.create(bind=engine, checkfirst=True)
    ensure_lightweight_schema_upgrades()
    warn_on_schema_drift()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
