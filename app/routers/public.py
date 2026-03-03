from __future__ import annotations
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import Event, Participant, EventParticipant, SpinResult, EventType
from ..auth import get_current_user_optional

router = APIRouter()


@router.get("/api/events/{eid}/state")
async def public_event_state(request: Request, eid: int, session: AsyncSession = Depends(get_session)):
    ev = await session.get(Event, eid)
    if not ev:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    # participants and weights
    links = await session.exec(select(EventParticipant).where(EventParticipant.event_id == ev.id))
    link_list = links.all()
    part_ids = [l.participant_id for l in link_list]
    parts = []
    if part_ids:
        parts = (await session.exec(select(Participant).where(Participant.id.in_(part_ids)))).all()
    links_map = {l.participant_id: (l.weight if l.weight and l.weight > 0 else None) for l in link_list}
    rp = request.scope.get("root_path") or ""
    def _pref(u: str | None):
        if not u:
            return u
        if u.startswith("http://") or u.startswith("https://") or u.startswith("//"):
            return u
        if u.startswith("/static/") and rp:
            return f"{rp}{u}"
        return u
    parts_full = [
        {
            "id": p.id,
            "name": p.name,
            "image_url": _pref(p.image_url),
            "weight": (links_map.get(p.id) or p.default_weight),
        } for p in parts
    ]
    # spin results and winner/finished time
    spin = (await session.exec(select(SpinResult).where(SpinResult.event_id == ev.id).order_by(SpinResult.created_at))).all()
    winner_id = None
    finished_at = None
    if ev.finished and spin:
        for r in reversed(spin):
            if not r.eliminated:
                winner_id = r.participant_id
                finished_at = r.created_at
                break
    stages = []
    if (ev.in_progress or ev.finished) and ev.event_type == EventType.reverse:
        for r in spin:
            stages.append({
                "time": (r.created_at.replace(tzinfo=None) if getattr(r.created_at, 'tzinfo', None) else r.created_at).isoformat()+"Z",
                "participant_id": r.participant_id,
                "eliminated": r.eliminated,
            })
    data = {
        "event": {
            "id": ev.id,
            "type": ev.event_type.value if hasattr(ev.event_type, 'value') else str(ev.event_type),
            "starts_at": (ev.starts_at.replace(tzinfo=None) if getattr(ev.starts_at, 'tzinfo', None) else ev.starts_at).isoformat()+"Z",
            "in_progress": ev.in_progress,
            "finished": ev.finished,
            "finished_at": ((finished_at.replace(tzinfo=None)).isoformat()+"Z") if finished_at else None,
            "slug": ev.slug,
            "description": ev.description,
        },
        "participants": parts_full,
        "stages": stages,
        "winner_id": winner_id,
    }
    return data


@router.get("/event/{slug}", response_class=HTMLResponse)
async def public_event(request: Request, slug: str, session: AsyncSession = Depends(get_session), user=Depends(get_current_user_optional)):
    res = await session.exec(
            select(Event)
            .options(selectinload(Event.prize))
            .where(Event.slug == slug)
        )
    ev = res.first()
    if not ev:
        raise HTTPException(status_code=404)
    # gather participants
    links = await session.exec(select(EventParticipant).where(EventParticipant.event_id == ev.id))
    link_list = links.all()
    part_ids = [l.participant_id for l in link_list]
    parts = []
    if part_ids:
        parts = (await session.exec(select(Participant).where(Participant.id.in_(part_ids)))).all()
    # build weights map
    links_map = {l.participant_id: (l.weight if l.weight and l.weight > 0 else None) for l in link_list}
    # participants detail with resolved weight
    rp = request.scope.get("root_path") or ""
    def _pref(u: str | None):
        if not u:
            return u
        if u.startswith("http://") or u.startswith("https://") or u.startswith("//"):
            return u
        if u.startswith("/static/") and rp:
            return f"{rp}{u}"
        return u
    parts_full = [
        {
            "id": p.id,
            "name": p.name,
            "image_url": _pref(p.image_url),
            "weight": (links_map.get(p.id) or p.default_weight),
        } for p in parts
    ]
    # spin results and winner/finished time
    spin = (await session.exec(select(SpinResult).where(SpinResult.event_id == ev.id).order_by(SpinResult.created_at))).all()
    winner_id = None
    finished_at = None
    if ev.finished and spin:
        # winner is the last non-eliminated result
        for r in reversed(spin):
            if not r.eliminated:
                winner_id = r.participant_id
                finished_at = r.created_at
                break
    # prepare stages for reverse
    stages = []
    if (ev.in_progress or ev.finished) and ev.event_type == EventType.reverse:
        for r in spin:
            stages.append({
                "time": r.created_at.isoformat(),
                "participant_id": r.participant_id,
                "eliminated": r.eliminated,
            })

    page_data = {
        "event": {
            "id": ev.id,
            "type": ev.event_type.value if hasattr(ev.event_type, 'value') else str(ev.event_type),
            "starts_at": (ev.starts_at.replace(tzinfo=None) if ev.starts_at.tzinfo else ev.starts_at).isoformat()+"Z",
            "in_progress": ev.in_progress,
            "finished": ev.finished,
            "finished_at": finished_at.isoformat() + "Z" if finished_at else None,
            "slug": ev.slug,
            "description": ev.description,
        },
        "participants": parts_full,
        "stages": stages,
        "winner_id": winner_id,
    }
    return request.app.templates.TemplateResponse("public/event.html", {"request": request, "user": user, "event": ev, "page_data": page_data})


@router.get("/search", response_class=HTMLResponse)
async def search_by_participant(request: Request, name: str | None = None, session: AsyncSession = Depends(get_session), user=Depends(get_current_user_optional)):
    events = []
    qname = (name or "").strip()
    if qname:
        # case-insensitive exact match on participant name
        from sqlalchemy import func
        parts = (await session.exec(select(Participant).where(func.lower(Participant.name) == qname.lower()))).all()
        if parts:
            ids = [p.id for p in parts]
            link_rows = (await session.exec(select(EventParticipant).where(EventParticipant.participant_id.in_(ids)))).all()
            ev_ids = list({lr.event_id for lr in link_rows})
            if ev_ids:
                events = (await session.exec(select(Event).where(Event.id.in_(ev_ids)))).all()
    return request.app.templates.TemplateResponse("public/search.html", {"request": request, "user": user, "events": events, "name": qname})
