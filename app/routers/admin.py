from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import User, Event, Participant
from ..auth import get_current_user, is_super_admin

router = APIRouter()


def ensure_admin(user: User):
    if not is_super_admin(user.username):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


@router.get("/users", response_class=HTMLResponse)
async def admin_users(request: Request, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    ensure_admin(user)
    # fetch users and stats
    users = (await session.exec(select(User).order_by(User.created_at.desc()))).all()
    # counts
    event_counts = {u.id: 0 for u in users}
    part_counts = {u.id: 0 for u in users}
    ev_rows = (await session.exec(select(Event.user_id, func.count(Event.id)).group_by(Event.user_id))).all()
    for uid, cnt in ev_rows:
        event_counts[uid] = cnt
    pt_rows = (await session.exec(select(Participant.user_id, func.count(Participant.id)).group_by(Participant.user_id))).all()
    for uid, cnt in pt_rows:
        part_counts[uid] = cnt
    return request.app.templates.TemplateResponse("admin/users.html", {"request": request, "user": user, "users": users, "event_counts": event_counts, "part_counts": part_counts})


@router.get("/users/{uid}", response_class=HTMLResponse)
async def admin_user_detail(request: Request, uid: int, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    ensure_admin(user)
    u = await session.get(User, uid)
    if not u:
        return RedirectResponse("/admin/users", status_code=303)
    events = (await session.exec(
        select(Event)
        .options(selectinload(Event.prize), selectinload(Event.participants))
        .where(Event.user_id == uid)
        .order_by(Event.starts_at.desc())
    )).all()
    parts = (await session.exec(select(Participant).where(Participant.user_id == uid).order_by(Participant.name))).all()
    return request.app.templates.TemplateResponse("admin/user_detail.html", {"request": request, "user": user, "u": u, "events": events, "participants": parts})
