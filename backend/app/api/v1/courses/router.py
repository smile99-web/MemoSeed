from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.course import Course
from app.models.course_package import CoursePackage
from app.models.learning_item import LearningItem
from app.models.user import User
from app.schemas.course import (
    CourseCreate,
    CoursePackageCreate,
    CoursePackageRead,
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
            .order_by(LearningItem.created_at.asc())
        ).all()

        export_courses.append(
            PackageExportCourse(
                name=course.name,
                description=course.description,
                items=[
                    PackageExportItem(
                        item_type=item.item_type,
                        english_text=item.english_text,
                        chinese_text=item.chinese_text,
                        phonetic=item.phonetic,
                        difficulty_level=item.difficulty_level,
                    )
                    for item in items
                ],
            )
        )

    return PackageExportData(
        version=1,
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
    for export_course in payload.courses:
        course = Course(
            user_id=current_user.id,
            package_id=package.id,
            name=export_course.name.strip(),
            description=export_course.description.strip(),
        )
        db.add(course)
        db.commit()
        db.refresh(course)

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
            )
            db.add(item)
            items_count += 1
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

    course = Course(user_id=current_user.id, package_id=payload.package_id, name=name, description=payload.description.strip())
    db.add(course)
    db.commit()
    db.refresh(course)
    return CourseRead.model_validate(course)


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
