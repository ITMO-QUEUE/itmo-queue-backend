from fastapi import APIRouter

# 1. Добавляем swaps в импорт
from itmo_queue.api.v1 import entries, groups, students, swaps

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(groups.router)
api_router.include_router(students.router)
api_router.include_router(entries.router)

# 2. Подключаем роутер обменов
api_router.include_router(swaps.router)
