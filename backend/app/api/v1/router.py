from fastapi import APIRouter

from app.api.v1.auth.router import router as auth_router
from app.api.v1.courses.router import router as courses_router
from app.api.v1.learning.router import router as learning_router
from app.api.v1.memory.router import router as memory_router
from app.api.v1.reports.router import router as reports_router
from app.api.v1.review.router import router as review_router
from app.api.v1.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(courses_router, prefix="/courses", tags=["courses"])
api_router.include_router(learning_router, prefix="/learning", tags=["learning"])
api_router.include_router(review_router, prefix="/review", tags=["review"])
api_router.include_router(memory_router, prefix="/memory", tags=["memory"])
api_router.include_router(reports_router, prefix="/reports", tags=["reports"])
