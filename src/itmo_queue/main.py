from fastapi import FastAPI

from itmo_queue.api.v1.router import api_router
from itmo_queue.core.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(api_router, prefix=settings.api_prefix)
