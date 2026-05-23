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


class PackageExportCourse(BaseModel):
    name: str
    description: str
    items: list[PackageExportItem] = []


class PackageExportData(BaseModel):
    version: int = 1
    package: CoursePackageCreate
    courses: list[PackageExportCourse] = []


class PackageImportResult(BaseModel):
    imported_package_name: str
    courses_count: int
    items_count: int
