from app.db.session import Base
from app.models.ai_daily_report import AiDailyReport
from app.models.course import Course
from app.models.course_completion_log import CourseCompletionLog
from app.models.course_package import CoursePackage
from app.models.daily_plan import DailyPlan
from app.models.generated_sentence import GeneratedSentence
# 2026-08-16: these three were missing, so Base.metadata never knew about
# grammar_sessions / grammar_answers / listening_stories — an Alembic
# autogenerate would have emitted drop_table() for live tables.
from app.models.grammar_session import GrammarAnswer, GrammarSession
from app.models.learning_event import LearningEvent, LearningMinuteStat
from app.models.listening_story import ListeningStory
from app.models.learning_item import LearningItem
from app.models.memory_state import MemoryState
from app.models.mistake_log import MistakeLog
from app.models.refresh_token import RefreshToken
from app.models.review_log import ReviewLog
from app.models.study_time_log import StudyTimeLog
from app.models.speech_asset import SpeechAsset
from app.models.tts_usage_log import TtsUsageLog
from app.models.user import User
from app.models.user_model_settings import UserModelSettings
from app.models.user_points import UserPoints, PointsLog
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.models.word_translation import WordTranslation

__all__ = [
    "AiDailyReport",
    "Base",
    "Course",
    "CourseCompletionLog",
    "CoursePackage",
    "DailyPlan",
    "GrammarAnswer",
    "GrammarSession",
    "LearningEvent",
    "LearningMinuteStat",
    "GeneratedSentence",
    "ListeningStory",
    "LearningItem",
    "MemoryState",
    "MistakeLog",
    "RefreshToken",
    "ReviewLog",
    "StudyTimeLog",
    "SpeechAsset",
    "TtsUsageLog",
    "User",
    "UserModelSettings",
    "UserPoints",
    "PointsLog",
    "WordMemoryState",
    "WordReviewTask",
    "WordTranslation",
]
