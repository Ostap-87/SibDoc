# -*- coding: utf-8 -*-
"""SibDoc3: закрытый контур — HTTP API (подключается в app/server.py одной строкой).

  GET  /api/offline                 — состояние контура: включён ли, разрешённые адреса,
                                      настройки «Обновления с носителя»
  POST /api/rates/import            — курсы из файлов (поле files, можно несколько и zip):
                                      ЦБ РФ XML_daily / XML_dynamic, CFETS Excel/CSV, шаблон;
                                      ?currency=USD — валюта для XML_dynamic без кода ЦБ,
                                      ?source=cbr|cfets|china_customs|manual — для шаблона без колонки «источник»
  GET  /api/rates/template          — шаблон Excel «дата, валюта, курс, источник»
  POST /api/offline/update          — обновление с носителя (поле file, архив .zip; только admin):
                                      ?apply=false — только проверить, ?apply=true — установить
  GET  /api/offline/updates         — последние обновления с носителя (из журнала)
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile

from app import media_update, netguard

router = APIRouter()


def _user(request: Request) -> str:
    return getattr(request.state, "user", "-")


def _max_bytes() -> int:
    try:
        from app.config_loader import load_config
        mb = int((load_config().get("server") or {}).get("max_upload_mb", 100))
    except Exception:  # noqa: BLE001
        mb = 100
    return mb * 1024 * 1024


async def _read(f: UploadFile, limit: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(413, f"Файл «{f.filename}» больше {limit // (1024 * 1024)} МБ")
    return bytes(buf)


@router.get("/api/offline")
def offline_status():
    st = netguard.status()
    upd = media_update.update_settings()
    st["update"] = {"require_signature": upd["require_signature"],
                    "trusted_keys": sorted(media_update.trusted_keys(upd["keys_dir"]))}
    return st


@router.post("/api/rates/import")
async def rates_import(request: Request, files: list[UploadFile] = File(...),
                       currency: str = "", source: str = ""):
    from app.rates.files import ACCEPTED, import_files
    limit = _max_bytes()
    payload, bad = [], []
    for f in files:
        name = f.filename or "file"
        if not name.lower().endswith(ACCEPTED):
            bad.append({"file": name, "ok": False,
                        "error": f"формат не поддерживается ({', '.join(ACCEPTED)})"})
            continue
        payload.append((await _read(f, limit), name))
    results = bad + import_files(payload, user=_user(request), currency=currency or None,
                                 source=source or None)
    return {"processed": len(results), "ok": sum(1 for r in results if r.get("ok")), "results": results}


@router.get("/api/rates/template")
def rates_template():
    from app.rates.files import template_xlsx
    return Response(template_xlsx(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=\"sibdoc_rates_template.xlsx\""})


@router.post("/api/offline/update")
async def offline_update(request: Request, file: UploadFile = File(...), apply: bool = False):
    if getattr(request.state, "role", "operator") != "admin":
        raise HTTPException(403, "нужна роль admin")
    data = await _read(file, _max_bytes())
    if not apply:
        return media_update.verify(data, file.filename or "update.zip")
    try:
        return media_update.apply(data, file.filename or "update.zip", user=_user(request))
    except media_update.UpdateError as exc:
        raise HTTPException(400, f"Обновление не установлено: {exc}")


@router.get("/api/offline/updates")
def offline_updates(limit: int = 20):
    return media_update.history(max(1, min(limit, 200)))


__all__ = ["router"]
