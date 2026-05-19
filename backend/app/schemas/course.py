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
