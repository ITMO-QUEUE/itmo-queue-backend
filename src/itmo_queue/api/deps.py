import hashlib
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyCookie
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.core.redis import get_redis
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session

SESSION_COOKIE = "itmo_session"
cookie_scheme = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)


def session_key(token: str) -> str:
    return "session:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_current_user(
    token: Annotated[str | None, Depends(cookie_scheme)],
    redis: Annotated[Redis, Depends(get_redis)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
    )
    if not token or len(token) > 128:
        raise unauthorized
    user_id = await redis.get(session_key(token))
    if not user_id:
        raise unauthorized
    try:
        parsed_id = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise unauthorized from None
    user = await session.get(User, parsed_id)
    if not user or not user.is_active:
        raise unauthorized
    return user


async def get_current_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin rights required")
    return user
