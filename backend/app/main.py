from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import engine
from app.models.course_completion_log import CourseCompletionLog
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask


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
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


@asynccontextmanager
async def lifespan(app: FastAPI):
    CourseCompletionLog.__table__.create(bind=engine, checkfirst=True)
    WordMemoryState.__table__.create(bind=engine, checkfirst=True)
    WordReviewTask.__table__.create(bind=engine, checkfirst=True)
    ensure_lightweight_schema_upgrades()
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
