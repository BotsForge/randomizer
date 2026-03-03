from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import List
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
import os
import uuid
from io import BytesIO
from PIL import Image
import re

from ..db import get_session
from ..models import Event, EventParticipant, Participant, PrizeItem, EventType, SpinResult
from ..auth import get_current_user

router = APIRouter()

# Upload settings (reuse participants uploads dir)
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_PRIZE_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_PRIZE_DIM = 500  # px


def normalize_prize_image(value: str | None) -> str | None:
    v = (value or "").strip()
    if v.casefold() in {"none", "null", "undefined"}:
        return None
    return v or None


def save_prize_image(content: bytes) -> str | None:
    if len(content) > MAX_PRIZE_IMAGE_SIZE:
        return None
    try:
        im = Image.open(BytesIO(content))
        im = im.convert("RGB")
        im.thumbnail((MAX_PRIZE_DIM, MAX_PRIZE_DIM))
        # Try multiple qualities to keep under ~1 MB
        for q in (70, 60, 50, 40):
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            if len(data) <= 1024 * 1024:  # <= 1MB target
                fname = f"{uuid.uuid4().hex}.jpg"
                dest_path = os.path.join(UPLOAD_DIR, fname)
                with open(dest_path, "wb") as f:
                    f.write(data)
                return f"/static/uploads/{fname}"
        # If still large, save last attempt
        fname = f"{uuid.uuid4().hex}.jpg"
        dest_path = os.path.join(UPLOAD_DIR, fname)
        with open(dest_path, "wb") as f:
            f.write(data)
        return f"/static/uploads/{fname}"
    except Exception:
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    if dt is None:
        return now_utc()
    if getattr(dt, "tzinfo", None) is None:
        # assume stored as UTC if naive
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/", response_class=HTMLResponse)
async def list_events(request: Request, q: str | None = None, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    # Load all user's events with prize preloaded; filter in Python with Unicode-aware casefold
    stmt = (
        select(Event)
        .where(Event.user_id == user.id)
        .options(
            selectinload(Event.prize),
            selectinload(Event.participants),
        )
        .order_by(Event.starts_at.desc())
    )
    res = await session.exec(stmt)
    events = res.unique().all()
    if q:
        qc = q.casefold()
        def matches(e: Event) -> bool:
            desc = (e.description or "").casefold()
            title = ((e.prize.title if e.prize else "") or "").casefold()
            return (qc in desc) or (qc in title)
        events = [e for e in events if matches(e)]
    return request.app.templates.TemplateResponse("events/list.html", {"request": request, "events": events, "q": q or "", "user": user})


@router.get("/new", response_class=HTMLResponse)
async def new_event(request: Request, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    # fetch user's participants for selection
    parts = (await session.exec(select(Participant).where(Participant.user_id == user.id).order_by(Participant.name))).all()
    return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "event": None, "participants": parts})


@router.post("/new")
async def create_event(request: Request,
                       description: str = Form(...),
                       slug: str = Form(...),
                       starts_at: str = Form(""),
                       starts_date: str = Form(""),
                       starts_time: str = Form(""),
                       event_type: str = Form("direct"),
                       prize_title: str = Form(""),
                       prize_image: str = Form(""),
                       prize_image_file: UploadFile | None = File(None),
                       participant_ids: List[int] = Form([]),
                       session: AsyncSession = Depends(get_session),
                       user=Depends(get_current_user)):
    # validate slug characters
    if not re.fullmatch(r"^[A-Za-z0-9_-]+$", (slug or "").strip()):
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "error": "Slug может содержать только латиницу, цифры, дефис и подчёркивание", "event": None, "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})
    # enforce slug uniqueness
    existing = await session.exec(select(Event).where(Event.slug == slug))
    if existing.first():
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "error": "Slug уже используется", "event": None, "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})

    # Parse datetime: prefer date+time in MSK, fallback to ISO starts_at
    try:
        if starts_date and starts_time:
            try:
                # naive in MSK
                naive = datetime.fromisoformat(f"{starts_date}T{starts_time}:00") if len(starts_time) == 5 else datetime.fromisoformat(f"{starts_date}T{starts_time}")
            except Exception:
                naive = datetime.fromisoformat(f"{starts_date}T{starts_time}")
            # convert MSK (UTC+3) to UTC
            dt = naive - timedelta(hours=3)
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(starts_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "error": "Некорректная дата/время", "event": None, "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})

    # prohibit past datetime
    if as_utc(dt) < now_utc():
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "error": "Нельзя указывать прошедшие дату и время", "event": None, "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})

    # Enforce max lengths
    description = (description or "").strip()[:1024]
    prize_title = (prize_title or "").strip()[:255]

    ev = Event(description=description, slug=slug.strip(), event_type=EventType(event_type), starts_at=dt, user_id=user.id)
    session.add(ev)
    await session.flush()

    # Prize processing: prefer uploaded file over URL
    final_prize_url = normalize_prize_image(prize_image)
    if prize_image_file and prize_image_file.filename:
        try:
            content = await prize_image_file.read()
            saved = save_prize_image(content)
            if saved:
                final_prize_url = saved
        except Exception:
            pass

    if prize_title:
        prize = PrizeItem(title=prize_title.strip(), image_url=final_prize_url, event_id=ev.id)
        session.add(prize)

    # limit to 1000 participants
    participant_ids = participant_ids[:1000]

    # Parse weights from dynamic fields: weight_{pid}
    formdata = await request.form()
    for pid in participant_ids:
        field = f"weight_{pid}"
        raw = (formdata.get(field) or "").strip()
        w = None
        if raw:
            try:
                wi = int(raw)
                if wi > 0:
                    w = wi
            except Exception:
                w = None
        link = EventParticipant(event_id=ev.id, participant_id=int(pid), weight=w)
        session.add(link)

    await session.commit()
    return RedirectResponse(request.url_for('list_events'), status_code=303)


@router.get("/check-slug")
async def check_slug(slug: str, exclude_id: int | None = None, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    # invalid pattern slugs are not available
    if not re.fullmatch(r"^[A-Za-z0-9_-]+$", (slug or "").strip()):
        return JSONResponse({"available": False})
    s = (await session.exec(select(Event).where(Event.slug == slug))).first()
    available = (s is None) or (exclude_id is not None and s.id == exclude_id)
    return JSONResponse({"available": bool(available)})


@router.get("/{eid}", response_class=HTMLResponse)
async def edit_event(request: Request, eid: int, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    res = await session.exec(
        select(Event)
        .options(
            selectinload(Event.prize),
            selectinload(Event.participants),
        )
        .where(Event.id == eid)
    )
    ev = res.first()
    if not ev or ev.user_id != user.id:
        return RedirectResponse(request.url_for('list_events'), status_code=303)
    parts = (await session.exec(select(Participant).where(Participant.user_id == user.id).order_by(Participant.name))).all()
    print(ev)
    print(ev.prize)
    print(ev.participants)
    return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "event": ev, "participants": parts})


@router.post("/{eid}")
async def update_event(request: Request,
                       eid: int,
                       description: str = Form(...),
                       slug: str = Form(...),
                       starts_at: str = Form(""),
                       starts_date: str = Form(""),
                       starts_time: str = Form(""),
                       event_type: str = Form("direct"),
                       prize_title: str = Form(""),
                       prize_image: str = Form(""),
                       prize_image_file: UploadFile | None = File(None),
                       participant_ids: List[int] = Form([]),
                       session: AsyncSession = Depends(get_session),
                       user=Depends(get_current_user)):
    # Load event with relationships to safely access in async context
    ev = (await session.exec(
        select(Event)
        .options(
            selectinload(Event.prize),
        )
        .where(Event.id == eid)
    )).first()
    if not ev or ev.user_id != user.id:
        return RedirectResponse(request.url_for('list_events'), status_code=303)
    if as_utc(ev.starts_at) <= now_utc():
        # cannot edit started/ongoing
        return RedirectResponse(request.url_for('list_events', eid=eid), status_code=303)
    # validate slug format
    if not re.fullmatch(r"^[A-Za-z0-9_-]+$", (slug or "").strip()):
        ev_full = (await session.exec(
            select(Event)
            .options(
                selectinload(Event.prize),
                selectinload(Event.participants),
            )
            .where(Event.id == ev.id)
        )).first()
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "event": ev_full or ev, "error": "Slug может содержать только латиницу, цифры, дефис и подчёркивание", "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})
    # check slug uniqueness
    other = await session.exec(select(Event).where(Event.slug == slug, Event.id != ev.id))
    if other.first():
        # Re-fetch event with relationships to avoid lazy-loading in templates
        ev_full = (await session.exec(
            select(Event)
            .options(
                selectinload(Event.prize),
                selectinload(Event.participants),
            )
            .where(Event.id == ev.id)
        )).first()
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "event": ev_full or ev, "error": "Slug уже используется", "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})

    # Parse datetime: prefer date+time in MSK, fallback to ISO starts_at
    try:
        if starts_date and starts_time:
            try:
                naive = datetime.fromisoformat(f"{starts_date}T{starts_time}:00") if len(starts_time) == 5 else datetime.fromisoformat(f"{starts_date}T{starts_time}")
            except Exception:
                naive = datetime.fromisoformat(f"{starts_date}T{starts_time}")
            dt = naive - timedelta(hours=3)
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(starts_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        ev_full = (await session.exec(
            select(Event)
            .options(
                selectinload(Event.prize),
                selectinload(Event.participants),
            )
            .where(Event.id == ev.id)
        )).first()
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "event": ev_full or ev, "error": "Некорректная дата/время", "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})

    # Prevent past datetime
    if as_utc(dt) < now_utc():
        ev_full = (await session.exec(
            select(Event)
            .options(
                selectinload(Event.prize),
                selectinload(Event.participants),
            )
            .where(Event.id == ev.id)
        )).first()
        return request.app.templates.TemplateResponse("events/form.html", {"request": request, "user": user, "event": ev_full or ev, "error": "Нельзя указывать прошедшие дату и время", "participants": (await session.exec(select(Participant).where(Participant.user_id == user.id))).all()})

    # Enforce max lengths
    description = (description or "").strip()[:1024]
    prize_title = (prize_title or "").strip()[:255]

    ev.description = description
    ev.slug = slug.strip()
    ev.event_type = EventType(event_type)
    ev.starts_at = dt
    
    # update prize (handle upload)
    final_prize_url = normalize_prize_image(prize_image)
    if prize_image_file and prize_image_file.filename:
        try:
            content = await prize_image_file.read()
            saved = save_prize_image(content)
            if saved:
                # delete old uploaded file if it was stored locally
                if ev.prize and ev.prize.image_url and ev.prize.image_url.startswith("/static/uploads/"):
                    try:
                        old_name = os.path.basename(ev.prize.image_url)
                        old_path = os.path.join(UPLOAD_DIR, old_name)
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    except Exception:
                        pass
                final_prize_url = saved
        except Exception:
            pass

    if prize_title:
        if ev.prize:
            # if clearing image and old was uploaded locally, delete file
            if (not final_prize_url) and ev.prize.image_url and ev.prize.image_url.startswith("/static/uploads/"):
                try:
                    old_name = os.path.basename(ev.prize.image_url)
                    old_path = os.path.join(UPLOAD_DIR, old_name)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    pass
            ev.prize.title = prize_title.strip()
            ev.prize.image_url = final_prize_url
        else:
            session.add(PrizeItem(title=prize_title.strip(), image_url=final_prize_url, event_id=ev.id))
    else:
        if ev.prize:
            # also remove old uploaded file to save disk
            if ev.prize.image_url and ev.prize.image_url.startswith("/static/uploads/"):
                try:
                    old_name = os.path.basename(ev.prize.image_url)
                    old_path = os.path.join(UPLOAD_DIR, old_name)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    pass
            await session.delete(ev.prize)
    
    # update links
    await session.exec(select(EventParticipant).where(EventParticipant.event_id == ev.id))
    # simplest: delete existing and recreate
    from sqlalchemy import text
    await session.exec(text("DELETE FROM eventparticipant WHERE event_id = :eid").bindparams(eid=ev.id))
    # limit 1000
    participant_ids = participant_ids[:1000]

    # Parse weights from dynamic fields
    formdata = await request.form()
    for pid in participant_ids:
        raw = (formdata.get(f"weight_{pid}") or "").strip()
        w = None
        if raw:
            try:
                wi = int(raw)
                if wi > 0:
                    w = wi
            except Exception:
                w = None
        session.add(EventParticipant(event_id=ev.id, participant_id=int(pid), weight=w))
    await session.commit()
    return RedirectResponse(request.url_for('list_events'), status_code=303)


@router.post("/{eid}/delete")
async def delete_event(request: Request, eid: int, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    # Load event with prize to handle file cleanup before DB deletes
    ev = (await session.exec(
        select(Event)
        .options(
            selectinload(Event.prize),
        )
        .where(Event.id == eid)
    )).first()
    if ev and ev.user_id == user.id:
        # Remember local prize image path (if any) for filesystem cleanup
        prize_image_url = ev.prize.image_url if ev.prize else None
        # Manually delete dependent rows to avoid PK blank-out assertion
        from sqlalchemy import text
        await session.exec(text("DELETE FROM spinresult WHERE event_id = :eid").bindparams(eid=ev.id))
        await session.exec(text("DELETE FROM eventparticipant WHERE event_id = :eid").bindparams(eid=ev.id))
        await session.exec(text("DELETE FROM prizeitem WHERE event_id = :eid").bindparams(eid=ev.id))
        # Finally delete the event itself
        await session.exec(text("DELETE FROM event WHERE id = :eid").bindparams(eid=ev.id))
        await session.commit()
        # Remove uploaded prize image file if it was stored locally
        if prize_image_url and prize_image_url.startswith("/static/uploads/"):
            try:
                old_name = os.path.basename(prize_image_url)
                old_path = os.path.join(UPLOAD_DIR, old_name)
                if os.path.exists(old_path):
                    os.remove(old_path)
            except Exception:
                pass
    return RedirectResponse(request.url_for('list_events'), status_code=303)


@router.get("/view/{slug}", response_class=HTMLResponse)
async def view_event_by_slug(request: Request, slug: str, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    # owner view page
    res = await session.exec(
        select(Event)
        .options(
            selectinload(Event.prize),
        )
        .where(Event.slug == slug, Event.user_id == user.id)
    )
    ev = res.first()
    if not ev:
        return RedirectResponse(request.url_for('list_events'), status_code=303)
    # participants of event
    links = await session.exec(select(EventParticipant).where(EventParticipant.event_id == ev.id))
    link_list = links.all()
    part_ids = [l.participant_id for l in link_list]
    parts = []
    if part_ids:
        parts = (await session.exec(select(Participant).where(Participant.id.in_(part_ids)))).all()
    # build weights map
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
    # spin results and winner/finished
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
    return request.app.templates.TemplateResponse("events/view.html", {"request": request, "user": user, "event": ev, "page_data": page_data})
