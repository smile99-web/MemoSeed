from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.learning_item import LearningItem
from app.models.user import User
from app.schemas.learning import LearningImportResponse, LearningItemCreate, LearningItemRead
from app.services.learning_import import SUPPORTED_IMPORT_EXTENSIONS, import_learning_items, parse_txt_import, parse_xlsx_import

router = APIRouter()


@router.get("/items", response_model=list[LearningItemRead])
def list_learning_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[LearningItemRead]:
    items = db.scalars(
        select(LearningItem).where(LearningItem.user_id == current_user.id).order_by(LearningItem.created_at.desc())
    ).all()
    return [LearningItemRead.model_validate(item) for item in items]


@router.post("/items", response_model=LearningItemRead, status_code=status.HTTP_201_CREATED)
def create_learning_item(
    payload: LearningItemCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LearningItemRead:
    existing_item = db.scalar(
        select(LearningItem).where(
            LearningItem.user_id == current_user.id,
            LearningItem.item_type == payload.item_type,
            LearningItem.english_text.ilike(payload.english_text.strip()),
        )
    )
    if existing_item is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Learning item already exists")

    learning_item = LearningItem(user_id=current_user.id, **payload.model_dump())
    db.add(learning_item)
    db.commit()
    db.refresh(learning_item)
    return LearningItemRead.model_validate(learning_item)


@router.get("/items/{item_id}", response_model=LearningItemRead)
def get_learning_item(
    item_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LearningItemRead:
    item = db.scalar(select(LearningItem).where(LearningItem.id == item_id, LearningItem.user_id == current_user.id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Learning item not found")
    return LearningItemRead.model_validate(item)


@router.post("/imports", response_model=LearningImportResponse, status_code=status.HTTP_201_CREATED)
async def import_learning_items_file(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
) -> LearningImportResponse:
    filename = file.filename or "uploaded-file"
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_IMPORT_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .txt and .xlsx files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    if extension == ".txt":
        parse_result = parse_txt_import(content, filename)
    else:
        parse_result = parse_xlsx_import(content, filename)

    imported_items, duplicate_skipped_items = import_learning_items(db, current_user.id, parse_result.items)
    skipped_items = [*parse_result.skipped_items, *duplicate_skipped_items]

    return LearningImportResponse(
        imported_count=len(imported_items),
        skipped_count=len(skipped_items),
        total_rows=parse_result.total_rows,
        items=[LearningItemRead.model_validate(item) for item in imported_items],
        skipped_items=skipped_items,
    )
