from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide one session per request; callers own transaction/commit boundaries.

    Authentication may start a transaction. Handlers flush/commit explicitly.
    Closing the session rolls back
    unfinished transactions, including when a handler raises an exception.
    """
    async with request.app.state.session_factory() as session:
        yield session
