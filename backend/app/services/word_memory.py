from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory_state import MemoryState
from app.models.word_memory_state import WordMemoryState
from app.models.word_review_task import WordReviewTask
from app.services.memory_dashboard import calculate_word_priority
from app.utils import normalize_word

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

TASK_TYPE_LABELS = {
    "chinese_to_english": "看中文拼英文",
    "listen_spell": "听英文拼英文",
    "english_to_chinese": "英文选中文",
    "match_translation": "中英文配对",
    "missing_letter": "缺字母填空",
    "cloze_sentence": "短句填空",
    "hidden_recall": "看 5 秒后隐藏重拼",
    "recall_word": "无提示拼写",
}


def get_or_create_word_memory_state(
    db: Session,
    user_id: UUID,
    word: str,
    learning_item_id: UUID | None = None,
    memory_state_id: UUID | None = None,
) -> WordMemoryState:
    normalized_word = normalize_word(word)
    word_state = db.scalar(select(WordMemoryState).where(WordMemoryState.user_id == user_id, WordMemoryState.word == normalized_word))
    if word_state is not None:
        if learning_item_id is not None:
            word_state.learning_item_id = learning_item_id
        if memory_state_id is not None:
            word_state.memory_state_id = memory_state_id
        return word_state

    word_state = WordMemoryState(
        user_id=user_id,
        word=normalized_word,
        learning_item_id=learning_item_id,
        memory_state_id=memory_state_id,
    )
    db.add(word_state)
    db.flush()
    return word_state


def sync_word_memory_from_review(
    db: Session,
    user_id: UUID,
    word: str,
    memory_state: MemoryState,
    review_mode: str,
    is_correct: bool,
    error_type: str | None,
    now: datetime | None = None,
) -> WordMemoryState:
    now = now or datetime.now(UTC)
    word_state = get_or_create_word_memory_state(db, user_id, word, memory_state.learning_item_id, memory_state.id)
    word_state.memory_strength = memory_state.memory_strength
    word_state.forget_risk = memory_state.forget_risk
    word_state.consecutive_correct_count = memory_state.consecutive_correct_count
    word_state.consecutive_error_count = memory_state.consecutive_error_count
    word_state.recall_correct_count = memory_state.recall_correct_count
    word_state.hinted_correct_count = memory_state.hinted_correct_count
    word_state.preview_correct_count = memory_state.preview_correct_count
    word_state.context_correct_count = memory_state.context_correct_count
    word_state.last_reviewed_at = now
    word_state.next_micro_review_at = memory_state.next_review_at

    if is_correct and review_mode.startswith("word-recall"):
        local_date = now.astimezone(LOCAL_TIMEZONE).date()
        if word_state.last_no_hint_correct_date != local_date:
            word_state.no_hint_correct_date_count += 1
            word_state.last_no_hint_correct_date = local_date
    if is_correct and review_mode.startswith("word-preview"):
        word_state.hidden_recall_correct_count += 1
        word_state.last_answer_seen_at = now
    if error_type:
        counts = dict(word_state.error_type_counts or {})
        counts[error_type] = int(counts.get(error_type, 0)) + (1 if is_correct else 2)
        word_state.error_type_counts = counts

    word_state.priority_score = calculate_word_memory_priority(word_state, now)
    word_state.status = derive_word_status(word_state)
    db.add(word_state)
    return word_state


def calculate_word_memory_priority(word_state: WordMemoryState, now: datetime) -> float:
    stats = type(
        "WordPriorityStats",
        (),
        {
            "mistake_count": max(word_state.consecutive_error_count, 0),
            "consecutive_error_count": word_state.consecutive_error_count,
            "preview_correct_count": word_state.preview_correct_count,
            "recall_correct_count": word_state.recall_correct_count,
            "last_reviewed_at": word_state.last_reviewed_at,
        },
    )()
    return calculate_word_priority(stats, word_state.memory_strength, word_state.forget_risk, word_state.next_micro_review_at, now)


def derive_word_status(word_state: WordMemoryState) -> str:
    if (
        word_state.memory_strength >= 0.82
        and word_state.recall_correct_count >= 3
        and word_state.no_hint_correct_date_count >= 3
        and word_state.consecutive_correct_count >= 3
        and word_state.consecutive_error_count == 0
    ):
        return "mastered"
    if word_state.memory_strength >= 0.72 and word_state.recall_correct_count >= 2 and word_state.no_hint_correct_date_count >= 2 and word_state.consecutive_error_count == 0:
        return "near_mastered"
    if word_state.consecutive_error_count >= 3 or word_state.priority_score >= 0.78:
        return "difficult"
    if word_state.preview_correct_count > word_state.recall_correct_count or word_state.last_answer_seen_at is not None:
        return "teaching"
    return "consolidating"


def schedule_micro_review_tasks_for_mistake(
    db: Session,
    user_id: UUID,
    word_state: WordMemoryState,
    prompt_text: str,
    source_learning_item_id: UUID | None,
    error_type: str,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    cancel_future_pending_tasks(db, user_id, word_state.word)
    plan = build_micro_review_plan(now, word_state.consecutive_error_count, error_type)
    word_state.micro_review_stage += 1
    word_state.next_micro_review_at = plan[0][1] if plan else word_state.next_micro_review_at

    task_counts = dict(word_state.task_type_counts or {})
    for task_type, due_at in plan:
        task_counts[task_type] = int(task_counts.get(task_type, 0)) + 1
        db.add(
            WordReviewTask(
                user_id=user_id,
                word_memory_state_id=word_state.id,
                learning_item_id=source_learning_item_id,
                word=word_state.word,
                task_type=task_type,
                prompt_text=build_task_prompt(task_type, word_state.word, prompt_text),
                expected_answer=word_state.word,
                choices=build_task_choices(task_type, word_state.word, prompt_text),
                priority_score=word_state.priority_score,
                status="pending",
                source=f"word-memory:{error_type}",
                due_at=due_at,
            )
        )
    word_state.task_type_counts = task_counts


def cancel_future_pending_tasks(db: Session, user_id: UUID, word: str) -> None:
    tasks = db.scalars(
        select(WordReviewTask).where(
            WordReviewTask.user_id == user_id,
            WordReviewTask.word == word,
            WordReviewTask.status == "pending",
        )
    ).all()
    for task in tasks:
        task.status = "superseded"


def build_micro_review_plan(now: datetime, consecutive_error_count: int, error_type: str) -> list[tuple[str, datetime]]:
    local_now = now.astimezone(LOCAL_TIMEZONE)
    end_of_day = local_now.replace(hour=20, minute=0, second=0, microsecond=0)
    if end_of_day <= local_now:
        end_of_day = local_now + timedelta(hours=2)
    end_of_day = end_of_day.astimezone(UTC)

    first_task = "hidden_recall" if error_type == "unknown" or consecutive_error_count >= 3 else "chinese_to_english"
    second_task = "missing_letter" if error_type in {"middle", "ending", "sequence", "missing-letter", "extra-letter"} else "listen_spell"
    return [
        (first_task, now),
        (second_task, now + timedelta(minutes=3)),
        ("listen_spell", now + timedelta(minutes=10)),
        ("english_to_chinese", now + timedelta(minutes=20)),
        ("match_translation", now + timedelta(minutes=30)),
        ("cloze_sentence", end_of_day),
        ("chinese_to_english", now + timedelta(days=1)),
        ("cloze_sentence", now + timedelta(days=3)),
        ("recall_word", now + timedelta(days=7)),
    ]


def build_task_prompt(task_type: str, word: str, fallback_prompt: str) -> str:
    if task_type == "listen_spell":
        return f"听英文发音，拼写这个单词：{word}"
    if task_type == "english_to_chinese":
        return f"选择 {word} 的中文意思"
    if task_type == "match_translation":
        return f"把 {word} 和正确中文配对"
    if task_type == "missing_letter":
        return f"补全缺失字母：{mask_middle_letters(word)}"
    if task_type == "cloze_sentence":
        return fallback_prompt or f"在短句中填入 {word}"
    if task_type == "hidden_recall":
        return f"先看 5 秒，再隐藏重拼：{word}"
    if task_type == "recall_word":
        return "无提示拼写这个单词"
    return f"根据中文意思拼写英文：{word}"


def build_task_choices(task_type: str, word: str, fallback_prompt: str) -> list[str]:
    if task_type not in {"english_to_chinese", "match_translation"}:
        return []
    correct_choice = word
    return [correct_choice, "不是这个意思", "还需要再练"]


def mask_middle_letters(word: str) -> str:
    if len(word) <= 2:
        return "_ " * len(word)
    return " ".join([word[0], *("_" for _ in word[1:-1]), word[-1]])


def complete_word_review_task(db: Session, user_id: UUID, task_id: UUID | None, is_correct: bool) -> None:
    if task_id is None:
        return
    task = db.scalar(select(WordReviewTask).where(WordReviewTask.id == task_id, WordReviewTask.user_id == user_id))
    if task is None or task.status != "pending":
        return
    task.status = "completed" if is_correct else "failed"
    task.completed_at = datetime.now(UTC)
