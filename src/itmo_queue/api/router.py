from fastapi import APIRouter

from itmo_queue.api.v1 import auth, entries, groups, sessions, students, swaps

api_router = APIRouter(prefix="/api/v1")
for router in (auth.router, groups.router, students.router, sessions.router,
               entries.router, swaps.router):
    api_router.include_router(router)
