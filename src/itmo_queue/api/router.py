from fastapi import APIRouter

# Импортируем наши роутеры
from itmo_queue.api.v1 import auth, entries, groups, sessions, students, swaps

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(groups.router)
api_router.include_router(students.router)
api_router.include_router(entries.router)
api_router.include_router(sessions.router)

api_router.include_router(swaps.router)
