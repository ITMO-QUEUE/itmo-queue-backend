from fastapi import APIRouter

from itmo_queue.api.v1 import entries, groups, students

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(groups.router)
api_router.include_router(students.router)
api_router.include_router(entries.router)
