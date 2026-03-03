from __future__ import annotations
import os
from typing import AsyncGenerator
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from .models import User
from .security import hash_password

os.makedirs("data", exist_ok=True)
DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/database.db")

engine = create_async_engine(DB_URL, echo=False, future=True)
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db():
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Seed super admin user if configured
    username = os.getenv("SUPER_ADMIN_USERNAME")
    password = os.getenv("SUPER_ADMIN_PASSWORD")
    if username and password:
        async with async_session() as session:
            res = await session.exec(select(User).where(User.username == username))
            user = res.first()
            if not user:
                user = User(username=username, password_hash=hash_password(password))
                session.add(user)
                await session.commit()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
