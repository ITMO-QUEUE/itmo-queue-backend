from fastapi import APIRouter

from itmo_queue.api.schemas.session import ErrorResponse
from itmo_queue.api.v1 import (
    admin,
    auth,
    defense,
    entries,
    groups,
    realtime,
    sessions,
    students,
    swaps,
    views,
)

api_router = APIRouter(
    prefix="/api/v1",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Action not permitted for this user"},
        404: {"model": ErrorResponse, "description": "Resource not found or unavailable"},
        409: {"model": ErrorResponse, "description": "Conflict with current state"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
)
for router in (
    auth.router,
    admin.router,
    groups.router,
    students.router,
    sessions.router,
    entries.router,
    swaps.router,
    defense.router,
    views.router,
    realtime.router,
):
    api_router.include_router(router)
