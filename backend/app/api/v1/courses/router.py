from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.course import Course
from app.models.course_package import CoursePackage
from app.models.learning_item import LearningItem
from app.models.user import User
from app.models.word_memory_state import WordMemoryState
from app.services.learning_import import resequence_course_items
from app.schemas.course import (
    CourseCreate,
    CourseLockInfo,
    CoursePackageCreate,
    CoursePackageRead,
    CourseProgressRead,
    CourseRead,
    PackageExportCourse,
    PackageExportData,
    PackageExportItem,
    PackageImportResult,
)

router = APIRouter()


@router.get("/packages", response_model=list[CoursePackageRead])
def list_course_packages(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CoursePackageRead]:
    packages = db.scalars(
        select(CoursePackage).where(CoursePackage.user_id == current_user.id).order_by(CoursePackage.created_at.desc())
    ).all()
    return [CoursePackageRead.model_validate(package) for package in packages]


@router.post("/packages", response_model=CoursePackageRead, status_code=status.HTTP_201_CREATED)
def create_course_package(
    payload: CoursePackageCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CoursePackageRead:
    name = payload.name.strip()
    existing_package = db.scalar(select(CoursePackage).where(CoursePackage.user_id == current_user.id, CoursePackage.name == name))
    if existing_package is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Course package already exists")

    package = CoursePackage(user_id=current_user.id, name=name, description=payload.description.strip())
    db.add(package)
    db.commit()
    db.refresh(package)
    return CoursePackageRead.model_validate(package)


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course_package(
    package_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    package = db.scalar(select(CoursePackage).where(CoursePackage.id == package_id, CoursePackage.user_id == current_user.id))
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course package not found")

    db.delete(package)
    db.commit()


@router.get("/packages/{package_id}/export", response_model=PackageExportData)
def export_course_package(
    package_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PackageExportData:
    package = db.scalar(select(CoursePackage).where(CoursePackage.id == package_id, CoursePackage.user_id == current_user.id))
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course package not found")

    courses = db.scalars(
        select(Course).where(Course.package_id == package_id, Course.user_id == current_user.id).order_by(Course.created_at.asc())
    ).all()

    export_courses: list[PackageExportCourse] = []
    for course in courses:
        items = db.scalars(
            select(LearningItem)
            .where(LearningItem.course_id == course.id, LearningItem.user_id == current_user.id)
            .order_by(LearningItem.sort_order.asc(), LearningItem.created_at.asc())
        ).all()

        export_courses.append(
            PackageExportCourse(
                id=course.id,
                name=course.name,
                description=course.description,
                prerequisite_course_id=course.prerequisite_course_id,
                min_mastery_ratio=course.min_mastery_ratio,
                items=[
                    PackageExportItem(
                        item_type=item.item_type,
                        english_text=item.english_text,
                        chinese_text=item.chinese_text,
                        phonetic=item.phonetic,
                        difficulty_level=item.difficulty_level,
                        sort_order=item.sort_order,
                        unit_label=item.unit_label,
                    )
                    for item in items
                ],
            )
        )

    return PackageExportData(
        version=2,
        package=CoursePackageCreate(name=package.name, description=package.description),
        courses=export_courses,
    )


@router.post("/packages/import", response_model=PackageImportResult, status_code=status.HTTP_201_CREATED)
def import_course_package(
    payload: PackageExportData,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PackageImportResult:
    base_name = payload.package.name.strip()
    package_name = base_name
    suffix = 2
    while db.scalar(select(CoursePackage).where(CoursePackage.user_id == current_user.id, CoursePackage.name == package_name)) is not None:
        package_name = f"{base_name} ({suffix})"
        suffix += 1

    package = CoursePackage(
        user_id=current_user.id,
        name=package_name,
        description=payload.package.description.strip(),
    )
    db.add(package)
    db.commit()
    db.refresh(package)

    items_count = 0
    imported_courses: list[Course] = []
    course_id_map: dict[UUID, UUID] = {}
    for export_course in payload.courses:
        course = Course(
            user_id=current_user.id,
            package_id=package.id,
            name=export_course.name.strip(),
            description=export_course.description.strip(),
            prerequisite_course_id=None,
            min_mastery_ratio=export_course.min_mastery_ratio,
        )
        db.add(course)
        db.commit()
        db.refresh(course)
        imported_courses.append(course)
        if export_course.id is not None:
            course_id_map[export_course.id] = course.id

        for export_item in export_course.items:
            if not export_item.english_text.strip():
                continue
            item = LearningItem(
                user_id=current_user.id,
                course_id=course.id,
                item_type=export_item.item_type,
                english_text=export_item.english_text.strip(),
                chinese_text=export_item.chinese_text.strip(),
                phonetic=export_item.phonetic,
                difficulty_level=export_item.difficulty_level,
                sort_order=export_item.sort_order,
                unit_label=export_item.unit_label,
            )
            db.add(item)
            items_count += 1
        db.commit()
        resequence_course_items(db, current_user.id, course.id)

    for index, export_course in enumerate(payload.courses):
        if export_course.prerequisite_course_id is None or index >= len(imported_courses):
            continue
        mapped_prerequisite_id = course_id_map.get(export_course.prerequisite_course_id)
        if mapped_prerequisite_id is None:
            continue
        imported_courses[index].prerequisite_course_id = mapped_prerequisite_id
    db.commit()

    return PackageImportResult(
        imported_package_name=package.name,
        courses_count=len(payload.courses),
        items_count=items_count,
    )


@router.get("/courses", response_model=list[CourseRead])
def list_courses(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    package_id: UUID | None = None,
) -> list[CourseRead]:
    statement = select(Course).where(Course.user_id == current_user.id)
    if package_id:
        statement = statement.where(Course.package_id == package_id)
    courses = db.scalars(statement.order_by(Course.created_at.desc())).all()
    return [CourseRead.model_validate(course) for course in courses]


@router.post("/courses", response_model=CourseRead, status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseRead:
    package = db.scalar(select(CoursePackage).where(CoursePackage.id == payload.package_id, CoursePackage.user_id == current_user.id))
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course package not found")

    name = payload.name.strip()
    existing_course = db.scalar(select(Course).where(Course.package_id == payload.package_id, Course.name == name))
    if existing_course is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Course already exists")

    if payload.prerequisite_course_id is not None:
        prerequisite = db.scalar(select(Course).where(Course.id == payload.prerequisite_course_id, Course.user_id == current_user.id))
        if prerequisite is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prerequisite course not found")

    course = Course(
        user_id=current_user.id,
        package_id=payload.package_id,
        name=name,
        description=payload.description.strip(),
        prerequisite_course_id=payload.prerequisite_course_id,
        min_mastery_ratio=payload.min_mastery_ratio,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return CourseRead.model_validate(course)


@router.get("/courses/{course_id}/progress", response_model=CourseProgressRead)
def get_course_progress(
    course_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseProgressRead:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    word_items = db.scalars(
        select(LearningItem).where(
            LearningItem.course_id == course_id,
            LearningItem.user_id == current_user.id,
            LearningItem.item_type == "word",
        )
    ).all()

    total_words = len(word_items)
    # Distinct words only — a word imported into several courses used to be
    # counted once per course (e.g. "can" appearing in 3 courses inflated the
    # course mastery buckets past total_words).
    word_texts = list({item.english_text.lower().strip() for item in word_items if item.english_text.strip()})

    status_counts: dict[str, int] = {"mastered": 0, "near_mastered": 0, "consolidating": 0, "teaching": 0, "difficult": 0}
    tracked_words = 0
    if word_texts:
        word_states = db.scalars(
            select(WordMemoryState).where(
                WordMemoryState.user_id == current_user.id,
                WordMemoryState.word.in_(word_texts),
            )
        ).all()
        tracked_words = len(word_states)
        for ws in word_states:
            word_status = ws.status or "teaching"
            if word_status in status_counts:
                status_counts[word_status] += 1
        # Distinct course words without a memory state are counted as "teaching"
        uncounted = len(word_texts) - sum(status_counts.values())
        if uncounted > 0:
            status_counts["teaching"] += uncounted

    return CourseProgressRead(
        course_id=course_id,
        course_name=course.name,
        total_words=total_words,
        mastered=status_counts["mastered"],
        near_mastered=status_counts["near_mastered"],
        consolidating=status_counts["consolidating"],
        teaching=status_counts["teaching"],
        difficult=status_counts["difficult"],
        tracked_words=tracked_words,
    )


@router.get("/courses/{course_id}/lock-status", response_model=CourseLockInfo)
def get_course_lock_status(
    course_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CourseLockInfo:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    is_locked = False
    prerequisite_course_name = None
    mastery_ratio = None

    if course.prerequisite_course_id is not None:
        prerequisite = db.scalar(select(Course).where(Course.id == course.prerequisite_course_id, Course.user_id == current_user.id))
        if prerequisite is not None:
            prerequisite_course_name = prerequisite.name
            word_items = db.scalars(
                select(LearningItem.english_text).where(
                    LearningItem.course_id == prerequisite.id,
                    LearningItem.user_id == current_user.id,
                    LearningItem.item_type == "word",
                )
            ).all()
            word_texts = [w.lower().strip() for w in word_items]
            total_words = len(word_texts)
            if total_words > 0:
                mastered_count = db.scalar(
                    select(func.count()).where(
                        WordMemoryState.user_id == current_user.id,
                        WordMemoryState.word.in_(word_texts),
                        WordMemoryState.status.in_(["mastered", "near_mastered"]),
                    )
                )
                mastery_ratio = (mastered_count or 0) / total_words
                is_locked = (mastery_ratio or 0) < course.min_mastery_ratio
            else:
                mastery_ratio = 0.0
                is_locked = False
        else:
            is_locked = False

    return CourseLockInfo(
        course_id=course_id,
        course_name=course.name,
        is_locked=is_locked,
        prerequisite_course_id=course.prerequisite_course_id,
        prerequisite_course_name=prerequisite_course_name,
        mastery_ratio=mastery_ratio,
        required_mastery_ratio=course.min_mastery_ratio,
    )


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == current_user.id))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    db.delete(course)
    db.commit()
