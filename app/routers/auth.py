from __future__ import annotations
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db import get_session
from ..models import User
from ..auth import hash_password, verify_password, is_super_admin, get_current_user_optional

router = APIRouter(tags=["auth"])


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request, user=Depends(get_current_user_optional)):
    return request.app.templates.TemplateResponse("register.html", {"request": request, "user": user})


@router.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...), session: AsyncSession = Depends(get_session)):
    username = username.strip()
    if not username or not password:
        return request.app.templates.TemplateResponse("register.html", {"request": request, "error": "Введите логин и пароль"})
    exists = await session.exec(select(User).where(User.username == username))
    if exists.first():
        return request.app.templates.TemplateResponse("register.html", {"request": request, "error": "Пользователь уже существует"})
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    request.session["uid"] = user.id
    request.session["uname"] = user.username
    return RedirectResponse(request.url_for('index'), status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, user=Depends(get_current_user_optional)):
    return request.app.templates.TemplateResponse("login.html", {"request": request, "user": user})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), session: AsyncSession = Depends(get_session)):
    res = await session.exec(select(User).where(User.username == username))
    user = res.first()
    if not user or not verify_password(password, user.password_hash):
        return request.app.templates.TemplateResponse("login.html", {"request": request, "error": "Неверные данные"})
    request.session["uid"] = user.id
    request.session["uname"] = user.username
    return RedirectResponse(request.url_for('index'), status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(request.url_for('index'), status_code=303)
