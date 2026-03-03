from __future__ import annotations
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
import os
import uuid
from io import BytesIO
from PIL import Image

from ..db import get_session
from ..models import Participant
from ..auth import get_current_user

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_DIM = 100  # pixels


@router.get("/", response_class=HTMLResponse)
async def list_participants(request: Request, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    res = await session.exec(select(Participant).where(Participant.user_id == user.id).order_by(Participant.name))
    parts = res.all()
    return request.app.templates.TemplateResponse("participants/list.html", {"request": request, "participants": parts, "user": user})


@router.get("/new", response_class=HTMLResponse)
async def new_participant(request: Request, user=Depends(get_current_user)):
    return request.app.templates.TemplateResponse("participants/form.html", {"request": request, "user": user, "participant": None})


@router.post("/new")
async def create_participant(request: Request,
                             name: str = Form(...),
                             default_weight: int = Form(1),
                             image_url: str = Form(""),
                             image: UploadFile | None = File(None),
                             session: AsyncSession = Depends(get_session),
                             user=Depends(get_current_user)):
    # Decide image source: uploaded file preferred over URL
    final_url: str | None = None
    error = None
    try:
        if image and image.filename:
            content = await image.read()
            if len(content) > MAX_IMAGE_SIZE:
                error = "Файл слишком большой. Максимум 5 МБ."
            else:
                # Process and shrink to MAX_DIM
                try:
                    im = Image.open(BytesIO(content))
                    im = im.convert("RGB")
                    im.thumbnail((MAX_DIM, MAX_DIM))
                    fname = f"{uuid.uuid4().hex}.jpg"
                    dest_path = os.path.join(UPLOAD_DIR, fname)
                    im.save(dest_path, format="JPEG", quality=75, optimize=True)
                    final_url = f"/static/uploads/{fname}"
                except Exception:
                    error = "Не удалось обработать изображение. Загрузите корректный файл."
        elif image_url:
            final_url = image_url.strip() or None
    except Exception:
        error = "Ошибка при загрузке файла. Попробуйте еще раз."

    if error:
        return request.app.templates.TemplateResponse(
            "participants/form.html",
            {"request": request, "user": user, "participant": None, "error": error},
            status_code=400,
        )

    p = Participant(name=name.strip(), default_weight=max(1, int(default_weight or 1)), image_url=final_url, user_id=user.id)
    session.add(p)
    await session.commit()
    return RedirectResponse(request.url_for("list_participants"), status_code=303)


@router.get("/{pid}", response_class=HTMLResponse)
async def edit_participant(request: Request, pid: int, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    p = await session.get(Participant, pid)
    if not p or p.user_id != user.id:
        return RedirectResponse(request.url_for("list_participants"), status_code=303)
    return request.app.templates.TemplateResponse("participants/form.html", {"request": request, "user": user, "participant": p})


@router.post("/{pid}")
async def update_participant(request: Request,
                             pid: int,
                             name: str = Form(...),
                             default_weight: int = Form(1),
                             image_url: str = Form(""),
                             image: UploadFile | None = File(None),
                             session: AsyncSession = Depends(get_session),
                             user=Depends(get_current_user)):
    p = await session.get(Participant, pid)
    if not p or p.user_id != user.id:
        return RedirectResponse("/participants/", status_code=303)

    error = None
    final_url = p.image_url
    try:
        if image and image.filename:
            content = await image.read()
            if len(content) > MAX_IMAGE_SIZE:
                error = "Файл слишком большой. Максимум 5 МБ."
            else:
                try:
                    im = Image.open(BytesIO(content))
                    im = im.convert("RGB")
                    im.thumbnail((MAX_DIM, MAX_DIM))
                    fname = f"{uuid.uuid4().hex}.jpg"
                    dest_path = os.path.join(UPLOAD_DIR, fname)
                    im.save(dest_path, format="JPEG", quality=75, optimize=True)
                    new_url = f"/static/uploads/{fname}"
                    # delete previous uploaded file if it was in uploads
                    if final_url and final_url.startswith("/static/uploads/"):
                        try:
                            old_name = os.path.basename(final_url)
                            old_path = os.path.join(UPLOAD_DIR, old_name)
                            if os.path.exists(old_path):
                                os.remove(old_path)
                        except Exception:
                            pass
                    final_url = new_url
                except Exception:
                    error = "Не удалось обработать изображение. Загрузите корректный файл."
        elif image_url is not None:
            # If URL provided explicitly (could be empty to clear)
            url = (image_url or "").strip()
            if url != final_url:
                # delete previous uploaded if clearing or switching to external
                if (not url) and final_url and final_url.startswith("/static/uploads/"):
                    try:
                        old_name = os.path.basename(final_url)
                        old_path = os.path.join(UPLOAD_DIR, old_name)
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    except Exception:
                        pass
            final_url = url or None
    except Exception:
        error = "Ошибка при загрузке файла. Попробуйте еще раз."

    if error:
        return request.app.templates.TemplateResponse(
            "participants/form.html",
            {"request": request, "user": user, "participant": p, "error": error},
            status_code=400,
        )

    p.name = name.strip()
    p.default_weight = max(1, int(default_weight or 1))
    p.image_url = final_url
    await session.commit()
    return RedirectResponse(request.url_for("list_participants"), status_code=303)


@router.post("/{pid}/delete")
async def delete_participant(request: Request, pid: int, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    p = await session.get(Participant, pid)
    if p and p.user_id == user.id:
        # remove uploaded avatar file if stored locally
        if p.image_url and p.image_url.startswith("/static/uploads/"):
            try:
                old_name = os.path.basename(p.image_url)
                old_path = os.path.join(UPLOAD_DIR, old_name)
                if os.path.exists(old_path):
                    os.remove(old_path)
            except Exception:
                pass
        await session.delete(p)
        await session.commit()
    return RedirectResponse(request.url_for("list_participants"), status_code=303)
