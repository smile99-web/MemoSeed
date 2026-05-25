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

ERROR_TYPE_TASK_STRATEGIES = {
    "first-letter": ["english_to_chinese", "chinese_to_english", "listen_spell", "cloze_sentence"],
    "meaning": ["english_to_chinese", "match_translation", "chinese_to_english", "cloze_sentence"],
    "middle": ["missing_letter", "hidden_recall", "listen_spell", "cloze_sentence"],
    "sequence": ["missing_letter", "hidden_recall", "listen_spell", "cloze_sentence"],
    "ending": ["missing_letter", "cloze_sentence", "hidden_recall", "recall_word"],
    "missing-letter": ["missing_letter", "hidden_recall", "listen_spell", "cloze_sentence"],
    "extra-letter": ["missing_letter", "hidden_recall", "listen_spell", "cloze_sentence"],
    "unknown": ["hidden_recall", "chinese_to_english", "listen_spell", "missing_letter", "cloze_sentence"],
}

TEACHING_TIPS = {
    "first-letter": "先确认中文意思，再听首音，最后拼首字母。",
    "meaning": "先把中文意思和英文单词配上，再进入拼写。",
    "middle": "按音节或字母块拆开，中间部分慢慢拼。",
    "sequence": "先看清字母顺序，再用缺字母题固定视觉记忆。",
    "ending": "重点看词尾，注意后缀、时态或单复数。",
    "missing-letter": "先数清字母个数，再补缺失位置。",
    "extra-letter": "先数清字母个数，去掉多余字母。",
    "unknown": "先看 5 秒建立印象，再隐藏重拼。",
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
    plan = build_micro_review_plan(now, word_state, error_type)
    word_state.micro_review_stage += 1
    word_state.next_micro_review_at = plan[0][1] if plan else word_state.next_micro_review_at

    task_counts = dict(word_state.task_type_counts or {})
    for task_type, due_at, priority_multiplier in plan:
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
                priority_score=round(min(max(word_state.priority_score * priority_multiplier, 0.05), 1.0), 2),
                status="pending",
                source=f"word-memory:{error_type}:{TEACHING_TIPS.get(error_type, '专项复习')}",
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


def build_micro_review_plan(now: datetime, word_state: WordMemoryState, error_type: str) -> list[tuple[str, datetime, float]]:
    local_now = now.astimezone(LOCAL_TIMEZONE)
    end_of_day = local_now.replace(hour=20, minute=0, second=0, microsecond=0)
    if end_of_day <= local_now:
        end_of_day = local_now + timedelta(hours=2)
    end_of_day = end_of_day.astimezone(UTC)

    task_sequence = choose_task_sequence(word_state, error_type)
    same_day_offsets = [
        timedelta(0),
        timedelta(minutes=3),
        timedelta(minutes=10),
        timedelta(minutes=20),
        timedelta(minutes=30),
        end_of_day - now,
    ]
    long_term_tasks = ["chinese_to_english", "cloze_sentence", "recall_word"]
    long_term_offsets = [timedelta(days=1), timedelta(days=3), timedelta(days=7)]
    if word_state.consecutive_error_count >= 4:
        long_term_tasks.insert(0, "hidden_recall")
        long_term_offsets.insert(0, timedelta(hours=2))

    plan: list[tuple[str, datetime, float]] = []
    for index, task_type in enumerate(task_sequence[: len(same_day_offsets)]):
        delay = max(same_day_offsets[index], timedelta(0))
        plan.append((task_type, now + delay, max(1.0 - index * 0.08, 0.62)))
    for index, task_type in enumerate(long_term_tasks):
        plan.append((task_type, now + long_term_offsets[index], max(0.72 - index * 0.08, 0.5)))
    return plan


def choose_task_sequence(word_state: WordMemoryState, error_type: str) -> list[str]:
    base_sequence = ERROR_TYPE_TASK_STRATEGIES.get(error_type, ["chinese_to_english", "listen_spell", "missing_letter", "cloze_sentence"])
    if word_state.consecutive_error_count >= 3 and "hidden_recall" not in base_sequence[:2]:
        base_sequence = ["hidden_recall", *base_sequence]
    if word_state.preview_correct_count > word_state.recall_correct_count and "recall_word" not in base_sequence:
        base_sequence = [*base_sequence, "recall_word"]

    task_counts = {str(key): int(value or 0) for key, value in (word_state.task_type_counts or {}).items()}
    deduped_sequence: list[str] = []
    for task_type in base_sequence:
        if task_type not in deduped_sequence:
            deduped_sequence.append(task_type)
    fallback_tasks = ["chinese_to_english", "listen_spell", "missing_letter", "english_to_chinese", "cloze_sentence", "recall_word"]
    for task_type in fallback_tasks:
        if task_type not in deduped_sequence:
            deduped_sequence.append(task_type)

    first_task = min(deduped_sequence[:4], key=lambda task_type: task_counts.get(task_type, 0))
    return [first_task, *[task_type for task_type in deduped_sequence if task_type != first_task]]


def build_task_prompt(task_type: str, word: str, fallback_prompt: str) -> str:
    if task_type == "listen_spell":
        return f"听英文发音，拼写这个单词：{word}"
    if task_type == "english_to_chinese":
        return f"选择 {word} 的中文意思"
    if task_type == "match_translation":
        return f"把 {word} 和正确中文配对"
    if task_type == "missing_letter":
        return f"补全缺失字母：{mask_learning_letters(word)}"
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


def mask_learning_letters(word: str) -> str:
    if len(word) <= 2:
        return "_ " * len(word)
    if len(word) <= 5:
        return " ".join([word[0], *("_" for _ in word[1:])])
    return " ".join([word[0], "_", "_", *list(word[3:-2]), "_", word[-1]])


def complete_word_review_task(db: Session, user_id: UUID, task_id: UUID | None, is_correct: bool) -> None:
    if task_id is None:
        return
    task = db.scalar(select(WordReviewTask).where(WordReviewTask.id == task_id, WordReviewTask.user_id == user_id))
    if task is None or task.status != "pending":
        return
    task.status = "completed" if is_correct else "failed"
    task.completed_at = datetime.now(UTC)
