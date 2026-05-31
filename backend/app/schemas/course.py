from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CoursePackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class CoursePackageRead(CoursePackageCreate):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CourseCreate(BaseModel):
    package_id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    prerequisite_course_id: UUID | None = None
    min_mastery_ratio: float = Field(default=0.75, ge=0.0, le=1.0)


class CourseRead(CourseCreate):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PackageExportItem(BaseModel):
    item_type: str
    english_text: str
    chinese_text: str
    phonetic: str | None = None
    difficulty_level: int = 1
    sort_order: int = 0
    unit_label: str | None = None


class PackageExportCourse(BaseModel):
    id: UUID | None = None
    name: str
    description: str
    prerequisite_course_id: UUID | None = None
    min_mastery_ratio: float = 0.75
    items: list[PackageExportItem] = []


class PackageExportData(BaseModel):
    version: int = 2
    package: CoursePackageCreate
    courses: list[PackageExportCourse] = []


class PackageImportResult(BaseModel):
    imported_package_name: str
    courses_count: int
    items_count: int


class CourseProgressRead(BaseModel):
    course_id: UUID
    course_name: str
    total_words: int
    mastered: int
    near_mastered: int
    consolidating: int
    teaching: int
    difficult: int

    model_config = {"from_attributes": True}


class CourseLockInfo(BaseModel):
    course_id: UUID
    course_name: str
    is_locked: bool
    prerequisite_course_id: UUID | None
    prerequisite_course_name: str | None
    mastery_ratio: float | None
    required_mastery_ratio: float
