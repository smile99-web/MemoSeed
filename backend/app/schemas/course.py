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
    # Import payloads are untrusted JSON: validate against the DB check
    # constraints (ck_learning_items_item_type, ck_learning_items_difficulty_level)
    # so a hand-edited file gets a clean 422 instead of a 500 mid-import
    # (2026-08-04 audit).
    item_type: str = Field(pattern="^(word|phrase|sentence)$")
    english_text: str = Field(min_length=1, max_length=500)
    chinese_text: str = Field(default="", max_length=500)
    phonetic: str | None = Field(default=None, max_length=200)
    difficulty_level: int = Field(default=1, ge=1, le=5)
    sort_order: int = 0
    unit_label: str | None = Field(default=None, max_length=100)


class PackageExportCourse(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=120)  # courses.name is VARCHAR(120)
    description: str = ""
    prerequisite_course_id: UUID | None = None
    min_mastery_ratio: float = Field(default=0.75, ge=0.0, le=1.0)
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
    # Distinct words in this course that have a WordMemoryState — because
    # learning_items can contain several rows for the same word (one per
    # course), summing the per-word status buckets can exceed the item count.
    tracked_words: int = 0

    model_config = {"from_attributes": True}


class CourseLockInfo(BaseModel):
    course_id: UUID
    course_name: str
    is_locked: bool
    prerequisite_course_id: UUID | None
    prerequisite_course_name: str | None
    mastery_ratio: float | None
    required_mastery_ratio: float
