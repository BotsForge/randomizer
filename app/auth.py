from __future__ import annotations
import os
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from .db import get_session
from .models import User
from .security import hash_password, verify_password


async def get_current_user(session: AsyncSession = Depends(get_session), request: Request = None) -> User:
    user_id = request.session.get("uid") if request else None
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = await session.get(User, user_id)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


async def get_current_user_optional(session: AsyncSession = Depends(get_session), request: Request = None) -> Optional[User]:
    user_id = request.session.get("uid") if request else None
    if not user_id:
        return None
    return await session.get(User, user_id)


def is_super_admin(username: str) -> bool:
    super_user = os.getenv("SUPER_ADMIN_USERNAME")
    return bool(super_user and username and username.lower() == super_user.lower())
