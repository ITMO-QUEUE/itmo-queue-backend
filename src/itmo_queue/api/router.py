from fastapi import APIRouter

# Импортируем наши роутеры
from itmo_queue.api.v1 import auth, groups, sessions, students

# Создаем главный роутер с префиксом /api/v1
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)

# Подключаем роутеры групп и студентов
api_router.include_router(groups.router)
api_router.include_router(students.router)
api_router.include_router(sessions.router)

# Register future module routers here with include_router(..., tags=[...]).
