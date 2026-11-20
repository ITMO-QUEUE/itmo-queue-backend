"""Create the first administrator: python -m itmo_queue.bootstrap (interactive)."""

import asyncio
import getpass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from itmo_queue.api.schemas.queue import UserCreate
from itmo_queue.core.config import get_settings
from itmo_queue.core.security import get_password_hash
from itmo_queue.db.models.user import User


async def create_admin(payload: UserCreate):
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if await db.scalar(
                select(User.id).where(or_(User.role == "admin", User.login == payload.login))
            ):
                raise ValueError(
                    "Administrator or login already exists; use the administration API"
                )
            db.add(
                User(
                    **payload.model_dump(exclude={"password"}),
                    password_hash=get_password_hash(payload.password),
                )
            )
            await db.commit()
    finally:
        await engine.dispose()


def main():
    name = input("Administrator name: ").strip()
    login = input("Login: ").strip()
    password = getpass.getpass("Password (8+ characters): ")
    if password != getpass.getpass("Repeat password: "):
        raise ValueError("Passwords do not match")
    asyncio.run(create_admin(UserCreate(name=name, login=login, password=password, role="admin")))
    print("Administrator created")


if __name__ == "__main__":
    main()
