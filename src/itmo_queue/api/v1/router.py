from fastapi import APIRouter

from itmo_queue.api.v1.endpoints import health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
# Domain routes are added here and should delegate to application use cases.
