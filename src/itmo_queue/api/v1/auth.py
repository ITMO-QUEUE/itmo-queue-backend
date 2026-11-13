import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from itmo_queue.api.deps import SESSION_COOKIE, cookie_scheme, get_current_user, session_key
from itmo_queue.core.redis import get_redis
from itmo_queue.core.security import verify_password
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class CurrentUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    login: str
    role: str


@router.post("/login", response_model=CurrentUser, operation_id="login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    old_token: Annotated[str | None, Depends(cookie_scheme)],
):
    user = await db.scalar(select(User).where(User.login == payload.login))
    if (
        not user
        or not user.is_active
        or not await run_in_threadpool(verify_password, payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = secrets.token_urlsafe(32)
    ttl = request.app.state.settings.SESSION_TTL_SECONDS
    await redis.set(session_key(token), str(user.id), ex=ttl)
    if old_token and len(old_token) <= 128:
        await redis.delete(session_key(old_token))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        secure=request.app.state.settings.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return CurrentUser(id=str(user.id), name=user.name, login=user.login, role=user.role)


@router.get("/me", response_model=CurrentUser, operation_id="getCurrentUser")
async def me(user: Annotated[User, Depends(get_current_user)]):
    return CurrentUser(id=str(user.id), name=user.name, login=user.login, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="logout")
async def logout(
    response: Response,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    token: Annotated[str | None, Depends(cookie_scheme)],
):
    if token and len(token) <= 128:
        await redis.delete(session_key(token))
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=request.app.state.settings.SESSION_COOKIE_SECURE,
        samesite="lax",
    )
