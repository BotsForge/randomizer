import asyncio
import os
from datetime import datetime, timezone
from typing import Optional
import contextlib
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

from .db import init_db, get_session
from .models import Event, SpinResult, EventType
from .auth import get_current_user_optional
from .routers import auth as auth_router
from .routers import participants as participants_router
from .routers import events as events_router
from .routers import admin as admin_router
from .routers import public as public_router

load_dotenv()

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.scheduler_task = asyncio.create_task(event_scheduler())
    try:
        yield
    finally:
        task: Optional[asyncio.Task] = getattr(app.state, "scheduler_task", None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

IS_DEV = os.getenv("ENV", "dev") == "dev"

app = FastAPI(
    title="Smith's Randomizer",
    lifespan=lifespan,
    root_path=None if IS_DEV else "/randomizer",
    servers=None if IS_DEV else [{"url": "/randomizer"}],
    docs_url="/docs" if IS_DEV else None,
    redoc_url="/redoc" if IS_DEV else None,
    openapi_url="/openapi.json" if IS_DEV else None,
)

BASE_DIR = Path(__file__).resolve().parent

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="matrix_sid")

# Static and templates
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
# expose to routers via request.app.templates
setattr(app, "templates", templates)
# expose some globals
templates.env.globals["SUPER_ADMIN_USERNAME"] = os.getenv("SUPER_ADMIN_USERNAME", "")
from datetime import datetime as _dt, timezone as _tz
templates.env.globals["now"] = lambda: _dt.now(_tz.utc)
from datetime import timedelta as _td

def to_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        # assume stored as UTC if naive
        return dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(_tz.utc)

def to_msk(dt):
    if dt is None:
        return None
    msk = _tz(_td(hours=3))
    if getattr(dt, "tzinfo", None) is None:
        # assume naive is UTC
        return dt.replace(tzinfo=_tz.utc).astimezone(msk)
    return dt.astimezone(msk)

# expose helpers
templates.env.globals["to_msk"] = to_msk
templates.env.filters["to_utc"] = to_utc

def _event_type_label(value: str) -> str:
    mapping = {
        "direct": "Прямой выбор",
        "reverse": "Обратный (по выбыванию)",
    }
    return mapping.get(value, value)

# Jinja filters
templates.env.filters["event_type_label"] = _event_type_label

# Routers
app.include_router(auth_router.router)
app.include_router(participants_router.router, prefix="/participants", tags=["participants"])
app.include_router(events_router.router, prefix="/events", tags=["events"])
app.include_router(admin_router.router, prefix="/admin", tags=["admin"])
app.include_router(public_router.router, tags=["public"])


# @app.middleware("http")
# async def log_requests(request: Request, call_next):
#     print("---- REQUEST ----")
#     print("url:", request.url)
#     print("base_url:", request.base_url)
#     print("root_path:", request.scope.get("root_path"))
#     print("path:", request.scope.get("path"))
#     print("raw_path:", request.scope.get("raw_path"))
#     print("-----------------")
#     response = await call_next(request)
#     return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user=Depends(get_current_user_optional)):
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.get("/formulas", response_class=HTMLResponse)
async def formulas_page(request: Request, user=Depends(get_current_user_optional)):
    return templates.TemplateResponse("formulas.html", {"request": request, "user": user, "title": "Формулы рандомизации"})


# Simple in-process pubsub for WebSocket broadcasting per event
class EventHub:
    def __init__(self):
        self._subs: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, event_id: int, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._subs.setdefault(event_id, set()).add(ws)

    async def unsubscribe(self, event_id: int, ws: WebSocket):
        async with self._lock:
            if event_id in self._subs:
                self._subs[event_id].discard(ws)
                if not self._subs[event_id]:
                    del self._subs[event_id]

    async def broadcast(self, event_id: int, data: dict):
        async with self._lock:
            conns = list(self._subs.get(event_id, set()))
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                # drop dead connections
                await self.unsubscribe(event_id, ws)


hub = EventHub()


@app.websocket("/ws/events/{event_id}")
async def event_ws(websocket: WebSocket, event_id: int):
    await hub.subscribe(event_id, websocket)
    try:
        while True:
            # keepalive / client pings ignored
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.unsubscribe(event_id, websocket)


async def event_scheduler():
    from sqlmodel import select
    from .db import async_session
    import contextlib as _ctx
    while True:
        try:
            async with async_session() as session:
                now = datetime.now(timezone.utc)
                result = await session.exec(select(Event).where(Event.starts_at <= now, Event.finished == False))
                to_start = result.all()
                for ev in to_start:
                    if not ev.in_progress:
                        ev.in_progress = True
                        await session.commit()
                        await session.refresh(ev)
                        asyncio.create_task(run_event(ev.id))
        except Exception:
            # avoid crashing
            pass
        await asyncio.sleep(2)


async def run_event(event_id: int):
    from sqlmodel import select
    from .db import async_session
    async with async_session() as session:
        ev = await session.get(Event, event_id)
        if not ev or ev.finished:
            return
        # build working list of participant ids and weights for this event
        parts = await ev.fetch_participants(session)
        # fetch link weights
        from sqlmodel import select
        from .models import EventParticipant
        link_rows = (await session.exec(select(EventParticipant).where(EventParticipant.event_id == ev.id))).all()
        wmap = {lr.participant_id: (lr.weight if lr.weight and lr.weight > 0 else None) for lr in link_rows}
        active = [(p.id, (wmap.get(p.id) or p.default_weight)) for p in parts]
        eliminated: list[int] = []
        # For direct type: one pick only
        while True:
            # Notify stage start (client shows 5s timer, wheel spins 4s)
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
            await hub.broadcast(event_id, {"type": "stage_start", "active": [pid for pid, _ in active], "eliminated": eliminated, "time": ts.isoformat().replace('+00:00','Z')})
            await asyncio.sleep(5)
            # pick
            if not active:
                break
            if ev.event_type == EventType.direct:
                # weighted random pick winner
                pick_id = weighted_pick(active)
                from datetime import datetime, timezone
                sr = SpinResult(event_id=event_id, participant_id=pick_id, eliminated=False)
                session.add(sr)
                ev.finished = True
                ev.in_progress = False
                await session.commit()
                finished_at = datetime.now(timezone.utc)
                await hub.broadcast(event_id, {"type": "pick", "participant_id": pick_id, "eliminated": False, "final": True, "finished_at": finished_at.isoformat().replace('+00:00','Z')})
                break
            else:
                # reverse: eliminate someone (using inverse weights => lower weight less likely to be eliminated)
                # To invert weights, we build weights w' = max_w - w + 1
                max_w = max(w for _, w in active)
                inverted = [(pid, max_w - w + 1) for pid, w in active]
                pick_id = weighted_pick(inverted)
                eliminated.append(pick_id)
                active = [(pid, w) for pid, w in active if pid != pick_id]
                sr = SpinResult(event_id=event_id, participant_id=pick_id, eliminated=True)
                session.add(sr)
                await session.commit()
                await hub.broadcast(event_id, {"type": "pick", "participant_id": pick_id, "eliminated": True, "final": len(active) == 1})
                if len(active) == 1:
                    # last remaining is winner
                    from datetime import datetime, timezone
                    winner_id = active[0][0]
                    sr2 = SpinResult(event_id=event_id, participant_id=winner_id, eliminated=False)
                    session.add(sr2)
                    ev.finished = True
                    ev.in_progress = False
                    await session.commit()
                    finished_at = datetime.now(timezone.utc)
                    await hub.broadcast(event_id, {"type": "pick", "participant_id": winner_id, "eliminated": False, "final": True, "finished_at": finished_at.isoformat().replace('+00:00','Z')})
                    break
            await asyncio.sleep(5)


def weighted_pick(population: list[tuple[int, int]]) -> int:
    import random
    total = sum(max(1, w) for _, w in population)
    r = random.randint(1, total)
    acc = 0
    for pid, w in population:
        acc += max(1, w)
        if r <= acc:
            return pid
    return population[-1][0]
