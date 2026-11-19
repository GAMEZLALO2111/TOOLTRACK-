"""Imágenes de herramientas: una por herramienta física y una por TN (la herramienta usa la del TN si no tiene la suya)."""
import base64
import binascii
import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import DB_PATH, SessionLocal
from models.inventory import Herramienta, NumeroHerramienta

router = APIRouter(tags=["tool images"])

IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "tool_images")
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


class ImageUpload(BaseModel):
    data: str
    realizado_por: Optional[str] = None


def _detect(raw: bytes) -> Optional[str]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def _decode(item: ImageUpload):
    text = item.data.strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="The image is not valid") from None
    if not raw:
        raise HTTPException(status_code=422, detail="The image is empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The image is larger than 3 MB")
    extension = _detect(raw)
    if extension is None:
        raise HTTPException(status_code=422, detail="Use a PNG, JPEG or WebP image")
    return raw, extension


def _save(raw: bytes, extension: str) -> str:
    os.makedirs(IMAGE_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{extension}"
    with open(os.path.join(IMAGE_DIR, name), "wb") as handle:
        handle.write(raw)
    return name


def _remove(name: Optional[str]):
    if name and os.path.basename(name) == name:
        try:
            os.remove(os.path.join(IMAGE_DIR, name))
        except OSError:
            pass


def _serve(name: Optional[str]):
    if not name or os.path.basename(name) != name or not os.path.isfile(os.path.join(IMAGE_DIR, name)):
        raise HTTPException(status_code=404, detail="No image")
    return FileResponse(
        os.path.join(IMAGE_DIR, name),
        media_type=MEDIA_TYPES.get(name.rsplit(".", 1)[-1], "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=30"},
    )


def image_source(tool, tn_row):
    """Which image the tool shows: its own, else the one of its TN."""
    if tool.imagen:
        return "tool"
    if tn_row is not None and tn_row.imagen:
        return "tn"
    return None


@router.get("/inventario/herramientas/{herramienta_id}/imagen")
def get_tool_image(herramienta_id: str):
    db = SessionLocal()
    try:
        tool = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
        if not tool:
            raise HTTPException(status_code=404, detail="Physical tool not found")
        tn_row = db.query(NumeroHerramienta).filter(NumeroHerramienta.tn == tool.tn).first() if tool.tn else None
        return _serve(tool.imagen or (tn_row.imagen if tn_row else None))
    finally:
        db.close()


@router.put("/inventario/herramientas/{herramienta_id}/imagen")
def put_tool_image(herramienta_id: str, item: ImageUpload):
    raw, extension = _decode(item)
    db = SessionLocal()
    try:
        tool = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
        if not tool:
            raise HTTPException(status_code=404, detail="Physical tool not found")
        old = tool.imagen
        tool.imagen = _save(raw, extension)
        db.commit()
        _remove(old)
        return {"id": tool.id, "image": "tool"}
    finally:
        db.close()


@router.delete("/inventario/herramientas/{herramienta_id}/imagen")
def delete_tool_image(herramienta_id: str):
    db = SessionLocal()
    try:
        tool = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
        if not tool:
            raise HTTPException(status_code=404, detail="Physical tool not found")
        old, tool.imagen = tool.imagen, None
        db.commit()
        _remove(old)
        return {"id": tool.id, "image": None}
    finally:
        db.close()


@router.get("/catalogo/tn/{tn}/imagen")
def get_tn_image(tn: str):
    db = SessionLocal()
    try:
        row = db.query(NumeroHerramienta).filter(NumeroHerramienta.tn == tn).first()
        if not row:
            raise HTTPException(status_code=404, detail="TN not found")
        return _serve(row.imagen)
    finally:
        db.close()


@router.put("/catalogo/tn/{tn}/imagen")
def put_tn_image(tn: str, item: ImageUpload):
    raw, extension = _decode(item)
    db = SessionLocal()
    try:
        row = db.query(NumeroHerramienta).filter(NumeroHerramienta.tn == tn).first()
        if not row:
            raise HTTPException(status_code=404, detail="TN not found")
        old = row.imagen
        row.imagen = _save(raw, extension)
        db.commit()
        _remove(old)
        return {"tn": row.tn, "image": "tn"}
    finally:
        db.close()


@router.delete("/catalogo/tn/{tn}/imagen")
def delete_tn_image(tn: str):
    db = SessionLocal()
    try:
        row = db.query(NumeroHerramienta).filter(NumeroHerramienta.tn == tn).first()
        if not row:
            raise HTTPException(status_code=404, detail="TN not found")
        old, row.imagen = row.imagen, None
        db.commit()
        _remove(old)
        return {"tn": row.tn, "image": None}
    finally:
        db.close()
