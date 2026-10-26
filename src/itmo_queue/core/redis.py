from fastapi import Request
from redis.asyncio import Redis


def get_redis(request: Request) -> Redis:
    """Return the application client; its pool is closed by the lifespan."""
    return request.app.state.redis
