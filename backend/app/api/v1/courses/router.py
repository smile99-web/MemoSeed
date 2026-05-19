from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.course import Course
from app.models.course_package import CoursePackage
from app.models.user import User
from app.schemas.course import CourseCreate, CoursePackageCreate, CoursePackageRead, CourseRead

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
