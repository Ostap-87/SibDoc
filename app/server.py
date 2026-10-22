# -*- coding: utf-8 -*-
from __future__ import annotations
"""PostControl Suite — HTTP API (FastAPI).

Эндпоинты:
  GET  /api/health                 — жив ли сервис
  GET  /api/config                 — системная конфигурация (источники, проверки)
  GET  /api/rules                  — список файлов правил
  GET  /api/rules/{file}           — прочитать правила проверки
  PUT  /api/rules/{file}           — обновить правила (без рестарта)
  POST /api/upload/{slot}          — загрузить файл в слот (xlsx/xls/csv)
  DELETE /api/upload/{slot}        — очистить слот
  GET  /api/sources                — состояние слотов
  POST /api/run                    — запустить проверку (DQ + все проверки)
  GET  /api/report/excel           — скачать Excel-отчёт
  GET  /api/report/pdf             — скачать PDF-отчёт

Запуск:  uvicorn app.server:app --host 0.0.0.0 --port 8000
"""
import io
import json
import os
import tempfile
from typing import Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import journal, security
from app.config_loader import load_config, load_rules, save_rules
from app.connectors import get_connector, list_connectors
from app.models import Source
from app.services import data_quality
from app.services.decl_parser import fields_summary, parse_declaration
from app.services.engine import run as engine_run
from app.services.loader import read_upload
from app.services.ocr_reader import available_languages, read_scan, tesseract_available
from app.services.scan_mapper import build_preview_rows, write_preview_excel
from app.services.report_excel import write_excel
from app.services.report_pdf import write_pdf

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STORAGE_DIR = os.path.join(PROJECT_ROOT, "storage")
REPORTS_DIR = os.path.join(STORAGE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

app = FastAPI(title="PostControl Suite API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

# Сессионное хранилище загруженных файлов (в памяти; плюс копия на диске).
_uploads: dict[str, Source] = {}
_scans: list[dict] = []
_last_result = {"run": None}

# ---------------------------------------------------------------------------
# Персистентность загрузок: файлы сохраняются на диск, поэтому переход
# «приложение <-> браузер» и перезапуск сервера ничего не теряют.
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
_SLOT_EXTS = (".xlsx", ".xls", ".csv")


def _persist_upload(slot: str, filename: str, content: bytes) -> None:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    ext = os.path.splitext(filename)[1].lower() or ".xlsx"
    with open(os.path.join(UPLOADS_DIR, f"{slot}{ext}"), "wb") as fh:
        fh.write(content)


def _remove_persisted(slot: str) -> None:
    for ext in _SLOT_EXTS:
        path = os.path.join(UPLOADS_DIR, f"{slot}{ext}")
        if os.path.exists(path):
            os.remove(path)


def _known_slots() -> list[str]:
    return list(_cfg().get("sources", {}).keys())


def _disk_slot_files() -> list[tuple[str, str]]:
    """Файлы, чьё имя совпадает со слотом ('wide_table.xlsx' -> wide_table)."""
    out: list[tuple[str, str]] = []
    if not os.path.isdir(UPLOADS_DIR):
        return out
    known = _known_slots()
    for name in sorted(os.listdir(UPLOADS_DIR)):
        stem, ext = os.path.splitext(name)
        if ext.lower() in _SLOT_EXTS and stem in known:
            out.append((stem, os.path.join(UPLOADS_DIR, name)))
    return out


def _slot_by_stem(stem: str) -> str | None:
    known = _known_slots()
    if stem in known:
        return stem
    for k in known:
        low = stem.lower()
        if low.startswith(k.lower() + "_") or low.startswith(k.lower() + "-"):
            return k
    return None


def _ingest_bytes(slot: str, filename: str, data: bytes) -> None:
    df = read_upload(io.BytesIO(data), filename)
    _uploads[slot] = Source(slot=slot, name=filename, df=df)


def _ingest_file(path: str) -> str | None:
    """Прочитать файл в слот по имени. -> слот или None."""
    stem = os.path.splitext(os.path.basename(path))[0]
    slot = _slot_by_stem(stem)
    if slot is None:
        return None
    with open(path, "rb") as fh:
        _ingest_bytes(slot, os.path.basename(path), fh.read())
    return slot


def _restore_uploads() -> list[str]:
    """Перечитать сохранённые файлы. -> список восстановленных слотов."""
    return [slot for slot, path in _disk_slot_files() if _ingest_file(path) == slot]


# ---------------------------------------------------------------------------
# Аутентификация: пропускаем /api/health и /api/auth/*, остальное по токену.
@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path == "/api/health" \
       or path.startswith("/api/auth/"):
        return await call_next(request)
    # режим без пароля: config/system.yaml -> auth: disabled: true
    if (_cfg().get("auth") or {}).get("disabled"):
        request.state.user = "admin"
        request.state.role = "admin"
        return await call_next(request)
    token = request.cookies.get("sibdoc_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    rec = security.get_session(token)
    if rec is None:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    request.state.user = rec["login"]
    request.state.role = rec.get("role")
    return await call_next(request)


def _user(request: Request) -> str:
    return getattr(request.state, "user", "-")


def _role(request: Request) -> str:
    return getattr(request.state, "role", "operator")


def _require_admin(request: Request) -> None:
    if _role(request) != "admin":
        raise HTTPException(403, "нужна роль admin")


def _cfg() -> dict:
    return load_config()


def _all_sources(cfg: dict) -> dict[str, Source]:
    """Загруженные файлы + встроенные справочники."""
    from app.services.loader import load_sources
    sources = load_sources({}, cfg)          # builtin-справочники
    for slot, src in _uploads.items():       # пользовательские файлы поверх
        sources[slot] = src
    return sources


# ------------------------------------------------------------------ meta
@app.get("/api/health")
def health():
    return {"status": "ok", "product": "PostControl Suite"}


@app.get("/api/config")
def get_config():
    cfg = _cfg()
    return {
        "product": cfg.get("product"),
        "sources": {slot: {"label": s.get("label"), "required": bool(s.get("required")),
                           "has_builtin": bool(s.get("builtin"))}
                    for slot, s in cfg.get("sources", {}).items()},
        "checks": {cid: {"enabled": c.get("enabled"), "rules": c.get("rules")}
                   for cid, c in cfg.get("checks", {}).items()},
    }


# ------------------------------------------------------------------ rules
@app.get("/api/rules")
def list_rules():
    cfg = _cfg()
    return [{"check": cid, "enabled": c.get("enabled"), "file": c.get("rules")}
            for cid, c in cfg.get("checks", {}).items()]


@app.get("/api/rules/{rule_file}")
def get_rule(rule_file: str):
    if "/" in rule_file or ".." in rule_file:
        raise HTTPException(400, "Недопустимое имя файла")
    return {"file": rule_file, "rules": load_rules(rule_file)}


@app.put("/api/rules/{rule_file}")
def put_rule(rule_file: str, rules: dict):
    if "/" in rule_file or ".." in rule_file:
        raise HTTPException(400, "Недопустимое имя файла")
    save_rules(rule_file, rules)
    return {"file": rule_file, "saved": True}


# ------------------------------------------------------------------ uploads
@app.post("/api/upload/{slot}")
async def upload(slot: str, request: Request, file: UploadFile = File(...)):
    cfg = _cfg()
    if slot not in cfg.get("sources", {}):
        raise HTTPException(400, f"Неизвестный слот: {slot}")
    content = await file.read()
    try:
        df = read_upload(io.BytesIO(content), file.filename)
    except Exception as exc:
        raise HTTPException(400, f"Не удалось прочитать файл: {exc}")
    _uploads[slot] = Source(slot=slot, name=file.filename, df=df)
    _persist_upload(slot, file.filename, content)
    journal.log("upload", _user(request), f"{slot}: {file.filename} ({len(df)} строк)")
    return {"slot": slot, "file": file.filename, "rows": len(df),
            "columns": len(df.columns)}


@app.delete("/api/upload/{slot}")
def clear_upload(slot: str, request: Request):
    _uploads.pop(slot, None)
    _remove_persisted(slot)
    journal.log("delete", _user(request), f"слот очищен: {slot}")
    return {"slot": slot, "cleared": True}


@app.get("/api/sources")
def sources_state():
    cfg = _cfg()
    out = []
    builtins = {slot: s.get("builtin") for slot, s in cfg.get("sources", {}).items()}
    for slot, sc in cfg.get("sources", {}).items():
        up = _uploads.get(slot)
        out.append({
            "slot": slot, "label": sc.get("label"),
            "required": bool(sc.get("required")),
            "loaded": up is not None,
            "file": up.name if up else None,
            "rows": up.rows if up else None,
            "builtin": builtins.get(slot),
        })
    return out


# ------------------------------------------------------------------ auth & users
class LoginBody(BaseModel):
    login: str
    password: str


class UserBody(BaseModel):
    login: str
    password: str
    role: str = "operator"


class PasswordBody(BaseModel):
    password: str


@app.post("/api/auth/login")
def auth_login(body: LoginBody, response: Response):
    # режим без пароля: любые учётные данные принимаются как admin
    if (_cfg().get("auth") or {}).get("disabled"):
        rec = {"login": "admin", "role": "admin"}
    else:
        rec = security.verify(body.login, body.password)
        if rec is None:
            journal.log("login", body.login, "неудачный вход")
            raise HTTPException(401, "неверный логин или пароль")
    token = security.create_session(rec["login"])
    response.set_cookie("sibdoc_token", token, httponly=True, samesite="lax",
                        max_age=security.SESSION_TTL_SEC)
    journal.log("login", rec["login"], "вход выполнен")
    return {"login": rec["login"], "role": rec["role"]}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    security.drop_session(request.cookies.get("sibdoc_token"))
    response.delete_cookie("sibdoc_token")
    journal.log("logout", _user(request), "выход")
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    if (_cfg().get("auth") or {}).get("disabled"):
        return {"login": "admin", "role": "admin"}
    token = request.cookies.get("sibdoc_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    rec = security.get_session(token)
    if rec is None:
        raise HTTPException(401, "unauthorized")
    user = security.load_users().get(rec["login"], {})
    return {"login": rec["login"], "role": user.get("role", "operator")}


@app.get("/api/users")
def users_list(request: Request):
    _require_admin(request)
    return security.list_users()


@app.post("/api/users")
def users_create(request: Request, body: UserBody):
    _require_admin(request)
    try:
        security.create_user(body.login, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    journal.log("user_add", _user(request), f"{body.login} (роль: {body.role})")
    return {"ok": True}


@app.delete("/api/users/{login}")
def users_delete(request: Request, login: str):
    _require_admin(request)
    if login == _user(request):
        raise HTTPException(400, "нельзя удалить самого себя")
    try:
        security.delete_user(login)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    journal.log("user_del", _user(request), login)
    return {"ok": True}


@app.post("/api/users/{login}/password")
def users_password(request: Request, login: str, body: PasswordBody):
    if _role(request) != "admin" and login != _user(request):
        raise HTTPException(403, "можно менять только свой пароль")
    try:
        security.set_password(login, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    journal.log("user_pw", _user(request), f"смена пароля: {login}")
    return {"ok": True}


# ------------------------------------------------------------------ journal
@app.get("/api/journal")
def journal_query(request: Request, day: str = "", q: str = ""):
    return {"entries": journal.query(day=day or None, q=q or None)}


@app.get("/api/journal/days")
def journal_days(request: Request, month: str = ""):
    """month = 'YYYY-MM' -> {'2026-10-01': 3, ...} для календаря."""
    return journal.days_in_month(month)


# ------------------------------------------------------------------ connectors
class ReloadBody(BaseModel):
    pull: bool = True   # заодно опросить включённые коннекторы


@app.get("/api/connectors")
def connectors_list(request: Request):
    return [c.public_dict() for c in list_connectors(_cfg())]


@app.post("/api/connectors/{cid}/test")
def connectors_test(request: Request, cid: str):
    conn = get_connector(_cfg(), cid)
    if conn is None:
        raise HTTPException(404, "коннектор не найден")
    return conn.test()


@app.post("/api/connectors/{cid}/pull")
def connectors_pull(request: Request, cid: str):
    conn = get_connector(_cfg(), cid)
    if conn is None:
        raise HTTPException(404, "коннектор не найден")
    try:
        got = conn.pull(UPLOADS_DIR)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    ingested = [s for s in (_ingest_file(item["path"]) for item in got) if s]
    journal.log("pull", _user(request),
                f"{cid}: {len(got)} файлов, в слоты: {', '.join(ingested) or '-'}")
    return {"pulled": got, "slots": ingested}


@app.post("/api/reload")
def reload_data(request: Request, body: "Optional[ReloadBody]" = None):
    """Перечитать сохранённые файлы и (опционально) опросить включённые коннекторы."""
    body = body or ReloadBody()
    pulled: list[dict] = []
    errors: list[str] = []
    if body.pull:
        for conn in list_connectors(_cfg()):
            if not conn.enabled:
                continue
            try:
                got = conn.pull(UPLOADS_DIR)
                pulled.extend({**item, "connector": conn.id} for item in got)
            except Exception as exc:
                errors.append(f"{conn.id}: {exc}")
    restored = _restore_uploads()
    journal.log("reload", _user(request),
                f"перечитано слотов: {len(restored)}"
                + (f", коннекторы: {len(pulled)}" if pulled else ""))
    return {"restored": restored, "pulled": pulled, "errors": errors,
            "sources": sources_state()}


# ------------------------------------------------------------------ scans (OCR)
SCAN_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")


@app.get("/api/scans/info")
def scans_info():
    return {"ocr_langs": available_languages(),
            "tesseract": tesseract_available(),
            "accepted": list(SCAN_EXTS),
            "loaded": len(_scans)}


@app.post("/api/scans/upload")
async def upload_scans(files: list[UploadFile] = File(...)):
    """Массовая загрузка сканов деклараций: OCR (ru+en+zh) -> извлечение полей."""
    results = []
    for f in files:
        if not f.filename.lower().endswith(SCAN_EXTS):
            results.append({"file": f.filename, "ok": False,
                            "error": f"Формат не поддерживается ({', '.join(SCAN_EXTS)})"})
            continue
        try:
            content = await f.read()
            ocr = read_scan(io.BytesIO(content), f.filename)
            parsed = parse_declaration(ocr["text"])
            rec = {"file": f.filename, "ok": True,
                   "pages": ocr["pages"], "langs": ocr["engine_langs"],
                   "text_excerpt": ocr["text"][:600],
                   "fields": parsed}
            _scans.append(rec)
            results.append(rec)
        except Exception as exc:
            results.append({"file": f.filename, "ok": False, "error": str(exc)})
    ok_n = sum(1 for r in results if r.get("ok"))
    return {"processed": len(results), "ok": ok_n, "results": results}


@app.get("/api/scans/preview")
def scans_preview(lang: str = "ru"):
    """Плоские строки предпросмотра для UI-таблицы."""
    rows = build_preview_rows(_scans)
    # локализованные подписи полей — по первой записи
    labels = {}
    if _scans:
        for item in fields_summary(_scans[0]["fields"], lang=lang):
            labels[item["field"]] = item["label"]
    return {"count": len(rows), "labels": labels, "rows": rows}


@app.delete("/api/scans")
def clear_scans():
    _scans.clear()
    return {"cleared": True}


@app.get("/api/scans/excel")
def scans_excel():
    """Excel предпросмотра: значения в нужных ячейках + раскраска по статусу."""
    if not _scans:
        raise HTTPException(404, "Сначала загрузите сканы (/api/scans/upload)")
    path = os.path.join(REPORTS_DIR, "scan_preview.xlsx")
    write_preview_excel(_scans, path)
    return FileResponse(path, filename="scan_preview.xlsx")


# ------------------------------------------------------------------ run
def _dq_frame(findings) -> pd.DataFrame:
    return pd.DataFrame([f.as_dict() for f in findings])


@app.post("/api/run")
def run_checks(request: Request):
    cfg = _cfg()
    sources = _all_sources(cfg)
    result = engine_run(sources, cfg)
    _last_result["run"] = result
    summary = {row["check_id"]: row for row in result.summary_rows()}
    journal.log("run", _user(request),
                f"ISSUE: {sum(int(r.get('ISSUE', 0)) for r in summary.values())}, "
                f"REVIEW: {sum(int(r.get('REVIEW', 0)) for r in summary.values())}, "
                f"DQ-ERROR: {result.dq_counts().get('ERROR', 0)}")

    excel_path = write_excel(result, cfg, os.path.join(REPORTS_DIR, cfg["output"]["excel_name"]))
    pdf_path = write_pdf(result, cfg, os.path.join(REPORTS_DIR, cfg["output"]["pdf_name"]))

    return {
        "summary": result.summary_rows(),
        "sources": result.sources_info,
        "data_quality": [f.as_dict() for f in result.findings],
        "dq_counts": result.dq_counts(),
        "total_issues": result.total_issues(),
        "duration_sec": result.duration_sec,
        "checks": {cid: {"title": out.title,
                         "counts": out.counts(),
                         "rows": out.df.fillna("").astype(str).to_dict("records")}
                   for cid, out in result.outputs.items()},
        "reports": {"excel": "/api/report/excel", "pdf": "/api/report/pdf"},
    }


@app.get("/api/report/excel")
def report_excel():
    path = os.path.join(REPORTS_DIR, _cfg()["output"]["excel_name"])
    if not os.path.exists(path):
        raise HTTPException(404, "Отчёт ещё не сформирован — запустите /api/run")
    return FileResponse(path, filename="post_control_report.xlsx")


@app.get("/api/report/pdf")
def report_pdf():
    path = os.path.join(REPORTS_DIR, _cfg()["output"]["pdf_name"])
    if not os.path.exists(path):
        raise HTTPException(404, "Отчёт ещё не сформирован — запустите /api/run")
    return FileResponse(path, filename="post_control_report.pdf")


# ------------------------------------------------------------------ static UI
static_dir = os.path.join(PROJECT_ROOT, "webui")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="webui")


# ------------------------------------------------------------------ startup
# Первый запуск: создать admin/admin, восстановить ранее загруженные файлы.
if security.ensure_admin():
    journal.log("user_add", "system",
                f"создан пользователь admin (пароль по умолчанию: "
                f"{security.DEFAULT_ADMIN_PASSWORD}) — смените его")
_restored = _restore_uploads()
if _restored:
    journal.log("reload", "system", f"восстановлены слоты: {', '.join(_restored)}")
