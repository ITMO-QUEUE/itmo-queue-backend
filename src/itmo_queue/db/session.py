from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide one session per request; callers own transaction/commit boundaries.

    Use `async with session.begin():` for writes. Closing the session rolls back
    unfinished transactions, including when a handler raises an exception.
    """
    async with request.app.state.session_factory() as session:
        yield session
