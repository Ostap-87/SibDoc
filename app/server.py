# -*- coding: utf-8 -*-
from __future__ import annotations
"""PostControl Suite (SibDoc) — HTTP API (FastAPI).

Основные эндпоинты (полный список — docs/AGENT_GUIDE.md, §5):
  GET  /api/health, /api/config, /api/fields, /api/labels
  GET  /api/sources                 — состояние слотов (как прочитан каждый файл)
  POST /api/upload/{slot}           — загрузить файл(ы) в слот (xlsx/xls/csv/xml/zip; поле file или files)
  GET  /api/sources/{slot}/preview  — таблица источника целиком, подписи RU/ZH
  GET  /api/declarations/{slot}     — ДТ РФ / 报关单 карточками (шапка + товары)
  GET/PUT /api/rules/{file}         — правила проверок (+ история и откат)
  POST /api/run                     — прогон: DQ + все проверки, отчёты Excel/PDF с номером прогона
  GET  /api/runs, /api/runs/{id}/excel|pdf, /api/report/excel|pdf (последний)
  /api/rates/*                      — курсы валют: официальные, ручные, настройки
  /api/connectors/*, /api/notifications/* — коннекторы и уведомления
  /api/journal, /api/journal/export — журнал событий
  /api/scans/*                      — сканы деклараций (OCR) и перенос в источники

Запуск:  uvicorn app.server:app --host 0.0.0.0 --port 8000
"""
import io
import os
import re
import shutil
from typing import Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import fields as fdict
from app import journal, security
from app.config_loader import (diff_rules, known_rule_files, load_config, load_rules, load_rules_version,
                               rules_history, save_rules)
from app.models import Source
from app.services import labels, runs
from app.services.decl_parser import fields_summary, parse_declaration
from app.services.engine import run as engine_run
from app.services.loader import ACCEPTED_EXTS, LoadError, load_sources, read_many
from app.services.ocr_reader import available_languages, read_scan, tesseract_available
from app.services.scan_mapper import build_preview_rows, write_preview_excel
from app.services.xml_decl import XmlDeclError

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STORAGE_DIR = os.path.join(PROJECT_ROOT, "storage")
REPORTS_DIR = os.path.join(STORAGE_DIR, "reports")
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
os.makedirs(REPORTS_DIR, exist_ok=True)


def _cfg() -> dict:
    return load_config()


app = FastAPI(title="PostControl Suite API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=(_cfg().get("server") or {}).get("cors_origins") or ["*"],
                   allow_methods=["*"], allow_headers=["*"])

# Загруженные источники (в памяти; файлы — на диске в storage/uploads/<слот>/).
_uploads: dict[str, Source] = {}
_scans: list[dict] = []
_last_result = {"run": None, "files": None}


# ---------------------------------------------------------------------------
# Хранение загрузок: storage/uploads/<слот>/<файлы>. Замена файла очищает папку
# слота целиком, поэтому после перезапуска не «воскресают» старые файлы.
def _slot_dir(slot: str) -> str:
    return os.path.join(UPLOADS_DIR, slot)


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "file")
    return re.sub(r"[\x00-\x1f<>:\"|?*\\/]", "_", base)[:180] or "file"


def _store_files(slot: str, files: list[tuple[bytes, str]], append: bool) -> None:
    d = _slot_dir(slot)
    if not append and os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    for data, name in files:
        with open(os.path.join(d, _safe_name(name)), "wb") as fh:
            fh.write(data)


def _slot_files(slot: str) -> list[tuple[bytes, str]]:
    d = _slot_dir(slot)
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.isfile(p) and name.lower().endswith(ACCEPTED_EXTS):
            with open(p, "rb") as fh:
                out.append((fh.read(), name))
    return out


def _migrate_legacy() -> None:
    """Старый формат (storage/uploads/<слот>.<расш>) -> папка слота; берётся самый свежий файл."""
    if not os.path.isdir(UPLOADS_DIR):
        return
    known = list(_cfg().get("sources", {}))
    for slot in known:
        legacy = [os.path.join(UPLOADS_DIR, f) for f in os.listdir(UPLOADS_DIR)
                  if os.path.splitext(f)[0] == slot and os.path.isfile(os.path.join(UPLOADS_DIR, f))]
        if not legacy:
            continue
        if not os.path.isdir(_slot_dir(slot)):
            newest = max(legacy, key=os.path.getmtime)
            os.makedirs(_slot_dir(slot), exist_ok=True)
            shutil.move(newest, os.path.join(_slot_dir(slot), os.path.basename(newest)))
        for p in legacy:
            if os.path.exists(p):
                os.remove(p)


def _ingest(slot: str, files: list[tuple[bytes, str]], cfg: dict) -> Source:
    df, info = read_many(files, slot, cfg)
    name = files[0][1] if len(files) == 1 else f"{files[0][1]} (+{len(files) - 1})"
    src = Source(slot=slot, name=name, df=df, info=info)
    _uploads[slot] = src
    return src


def _restore_uploads() -> list[str]:
    cfg = _cfg()
    restored = []
    for slot in cfg.get("sources", {}):
        files = _slot_files(slot)
        if not files:
            continue
        try:
            _ingest(slot, files, cfg)
            restored.append(slot)
        except Exception as exc:  # noqa: BLE001 — повреждённый файл не должен ронять сервер
            journal.log("reload", "system", f"не удалось восстановить {slot}: {exc}")
    return restored


def _slot_for_filename(name: str, cfg: dict) -> str | None:
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    for k in cfg.get("sources", {}):
        if stem == k or stem.startswith(k + "_") or stem.startswith(k + "-"):
            return k
    return None


# ---------------------------------------------------------------------------
# Аутентификация: пропускаем /api/health и /api/auth/*, остальное по токену.
def _auth_disabled() -> bool:
    return bool((_cfg().get("auth") or {}).get("disabled"))


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path == "/api/health" or path.startswith("/api/auth/"):
        return await call_next(request)
    if _auth_disabled():                      # прототип: вход без пароля
        request.state.user = "prototype"
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


def _all_sources(cfg: dict) -> dict[str, Source]:
    """Загруженные файлы + встроенные справочники."""
    sources = load_sources({}, cfg)
    sources.update(_uploads)
    return sources


def _max_bytes(cfg: dict) -> int:
    return int((cfg.get("server") or {}).get("max_upload_mb", 100)) * 1024 * 1024


async def _read_limited(f: UploadFile, limit: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(413, f"Файл «{f.filename}» больше {limit // (1024 * 1024)} МБ")
    return bytes(buf)


def _jsonable(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    d = df.head(limit) if limit else df
    return d.astype(object).where(pd.notna(d), "").astype(str).replace({"nan": ""}).to_dict("records")


# ------------------------------------------------------------------ meta
@app.get("/api/health")
def health():
    return {"status": "ok", "product": "PostControl Suite", "version": app.version}


@app.get("/api/config")
def get_config():
    cfg = _cfg()
    return {
        "product": cfg.get("product"),
        "auth_disabled": _auth_disabled(),
        "max_upload_mb": int((cfg.get("server") or {}).get("max_upload_mb", 100)),
        "accepted": list(ACCEPTED_EXTS),
        "sources": {slot: {"label": s.get("label"), "required": bool(s.get("required")),
                           "multi": bool(s.get("multi")), "has_builtin": bool(s.get("builtin"))}
                    for slot, s in cfg.get("sources", {}).items()},
        "checks": {cid: {"enabled": c.get("enabled"), "rules": c.get("rules"),
                         "title": labels.check_title(cid), "title_zh": labels.check_title(cid, "zh")}
                   for cid, c in cfg.get("checks", {}).items()},
    }


@app.get("/api/fields")
def get_fields():
    return fdict.public()


@app.get("/api/labels")
def get_labels():
    return labels.public()


# ------------------------------------------------------------------ rules
@app.get("/api/rules")
def list_rules():
    cfg = _cfg()
    return [{"check": cid, "title": labels.check_title(cid), "enabled": c.get("enabled"), "file": c.get("rules")}
            for cid, c in cfg.get("checks", {}).items()]


def _check_rule_file(rule_file: str) -> None:
    if rule_file not in known_rule_files(_cfg()):
        raise HTTPException(400, "Недопустимое имя файла правил")


@app.get("/api/rules/{rule_file}")
def get_rule(rule_file: str):
    _check_rule_file(rule_file)
    return {"file": rule_file, "rules": load_rules(rule_file), "history": rules_history(rule_file)}


@app.put("/api/rules/{rule_file}")
def put_rule(rule_file: str, rules: dict, request: Request):
    _check_rule_file(rule_file)
    _require_admin(request)
    try:
        res = save_rules(rule_file, rules)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    changes = ", ".join(f"{d['key']}: {d['old']!r} → {d['new']!r}" for d in res["diff"]) or "без изменений"
    journal.log("rules_change", _user(request), f"{rule_file}: {changes}",
                meta={"file": rule_file, "diff": res["diff"], "backup": os.path.basename(res["backup"])})
    return {"file": rule_file, "saved": True, "diff": res["diff"]}


@app.get("/api/rules/{rule_file}/history/{version}")
def get_rule_version(rule_file: str, version: str):
    _check_rule_file(rule_file)
    try:
        old = load_rules_version(rule_file, version)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"file": rule_file, "version": version, "rules": old, "diff_to_current": diff_rules(old, load_rules(rule_file))}


@app.post("/api/rules/{rule_file}/restore/{version}")
def restore_rule(rule_file: str, version: str, request: Request):
    _check_rule_file(rule_file)
    _require_admin(request)
    try:
        old = load_rules_version(rule_file, version)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    res = save_rules(rule_file, old)
    journal.log("rules_restore", _user(request), f"{rule_file}: восстановлена версия {version}",
                meta={"file": rule_file, "version": version, "diff": res["diff"]})
    return {"file": rule_file, "restored": version, "diff": res["diff"]}


# ------------------------------------------------------------------ uploads
@app.post("/api/upload/{slot}")
async def upload(slot: str, request: Request, file: Optional[UploadFile] = File(None),
                 files: Optional[list[UploadFile]] = File(None), append: bool = False):
    cfg = _cfg()
    if slot not in cfg.get("sources", {}):
        raise HTTPException(400, f"Неизвестный слот: {slot}")
    items = [f for f in ([file] if file else []) + list(files or []) if f is not None]
    if not items:
        raise HTTPException(400, "Не передан файл")
    multi = bool(cfg["sources"][slot].get("multi"))
    if len(items) > 1 and not multi:
        raise HTTPException(400, "В этот слот загружается один файл")
    limit = _max_bytes(cfg)
    payload = [(await _read_limited(f, limit), _safe_name(f.filename)) for f in items]
    if append and multi:
        payload = _slot_files(slot) + payload
    try:
        src = _ingest(slot, payload, cfg)
    except (LoadError, XmlDeclError, ValueError) as exc:
        raise HTTPException(400, f"Не удалось прочитать файл: {exc}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Не удалось прочитать файл: {exc}")
    _store_files(slot, payload, append=False)
    info = src.info
    journal.log("upload", _user(request),
                f"{slot}: {', '.join(n for _, n in payload[:5])}{' …' if len(payload) > 5 else ''} "
                f"({len(src.df)} строк, колонок по словарю: {len(info.get('mapped') or [])})",
                meta={"slot": slot, "files": [n for _, n in payload], "rows": len(src.df),
                      "mapped": info.get("mapped"), "warnings": info.get("warnings")})
    return {"slot": slot, "file": src.name, "rows": len(src.df), "columns": len(src.df.columns),
            "info": info}


@app.delete("/api/upload/{slot}")
def clear_upload(slot: str, request: Request):
    _uploads.pop(slot, None)
    if os.path.isdir(_slot_dir(slot)):
        shutil.rmtree(_slot_dir(slot))
    journal.log("delete", _user(request), f"источник очищен: {slot}")
    return {"slot": slot, "cleared": True}


@app.get("/api/sources")
def sources_state():
    cfg = _cfg()
    out = []
    for slot, sc in cfg.get("sources", {}).items():
        up = _uploads.get(slot)
        info = up.info if up is not None else {}
        out.append({
            "slot": slot, "label": sc.get("label"), "label_en": sc.get("label_en"), "label_zh": sc.get("label_zh"),
            "required": bool(sc.get("required")), "multi": bool(sc.get("multi")),
            "loaded": up is not None,
            "file": up.name if up else None,
            "rows": up.rows if up else None,
            "columns": len(up.df.columns) if up else None,
            "files": info.get("files") or ([up.name] if up else []),
            "mapped": info.get("mapped") or [],
            "warnings": info.get("warnings") or [],
            "parts": [{k: p.get(k) for k in ("file", "format", "sheet", "encoding", "delimiter", "profile",
                                              "header_row", "rows")} for p in info.get("parts") or []],
            "builtin": sc.get("builtin"),
        })
    return out


@app.get("/api/sources/{slot}/preview")
def source_preview(slot: str, limit: int = 300, lang: str = "ru"):
    cfg = _cfg()
    src = _all_sources(cfg).get(slot)
    if src is None:
        raise HTTPException(404, "Источник не загружен")
    sf = ((cfg.get("sources") or {}).get(slot) or {}).get("fields")
    cols = [{"key": c, "label": labels.column(c, lang, sf), "label_ru": labels.column(c, "ru", sf),
             "label_zh": labels.column(c, "zh", sf), "field": fdict.field_of_column(c, sf)}
            for c in map(str, src.df.columns)]
    return {"slot": slot, "file": src.name, "rows_total": len(src.df), "columns": cols,
            "rows": _jsonable(src.df, max(1, min(limit, 5000))), "info": src.info}


@app.get("/api/declarations/{slot}")
def declarations(slot: str):
    """ДТ РФ / 报关单 КНР карточками: шапка (поля с одним значением) + товары."""
    if slot not in ("ru_dt", "cn_dec"):
        raise HTTPException(400, "Только ru_dt или cn_dec")
    src = _uploads.get(slot)
    if src is None or src.df.empty:
        return {"slot": slot, "declarations": []}
    df = src.df
    key = "decl_ru" if slot == "ru_dt" else "decl_cn"
    goods_cols = [c for c in ("item_no", "hs_code", "product_name", "model", "decl_elements", "container", "qty",
                              "unit", "unit_price", "total_value", "currency", "net_weight", "gross_weight",
                              "customs_value", "statistical_value", "origin_country") if c in df.columns]
    groups = df.groupby(key, sort=False) if key in df.columns else [("(без номера)", df)]
    out = []
    for num, grp in groups:
        header = []
        for c in grp.columns:
            if c in goods_cols or c == key:
                continue
            vals = [v for v in grp[c].tolist() if str(v).strip() not in ("", "nan", "None")]
            if vals and len(set(map(str, vals))) == 1:
                header.append({"key": c, "label_ru": labels.column(c, "ru"), "label_zh": labels.column(c, "zh"),
                               "value": str(vals[0])})
        out.append({"number": str(num), "header": header,
                    "goods_columns": [{"key": c, "label_ru": labels.column(c, "ru"),
                                       "label_zh": labels.column(c, "zh")} for c in goods_cols],
                    "goods": _jsonable(grp[goods_cols]) if goods_cols else []})
    return {"slot": slot, "count": len(out), "declarations": out}


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
    if _auth_disabled():
        rec = {"login": "prototype", "role": "admin"}
    else:
        rec = security.verify(body.login, body.password)
        if rec is None:
            journal.log("login", body.login, "неудачный вход")
            raise HTTPException(401, "неверный логин или пароль")
    token = security.create_session(rec["login"])
    response.set_cookie("sibdoc_token", token, httponly=True, samesite="lax", max_age=security.SESSION_TTL_SEC)
    journal.log("login", rec["login"], "вход выполнен")
    return {"login": rec["login"], "role": rec["role"], "must_change": rec.get("must_change", False)}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    security.drop_session(request.cookies.get("sibdoc_token"))
    response.delete_cookie("sibdoc_token")
    journal.log("logout", _user(request), "выход")
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    if _auth_disabled():
        return {"login": "prototype", "role": "admin", "auth_disabled": True}
    token = request.cookies.get("sibdoc_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    rec = security.get_session(token)
    if rec is None:
        raise HTTPException(401, "unauthorized")
    user = security.load_users().get(rec["login"], {})
    return {"login": rec["login"], "role": user.get("role", "operator"), "auth_disabled": False,
            "must_change": bool(user.get("must_change"))}


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
def journal_query(day: str = "", q: str = "", kind: str = "", date_from: str = "", date_to: str = "",
                  limit: int = 500):
    entries = journal.query(day=day or None, q=q or None, kind=kind or None, date_from=date_from or None,
                            date_to=date_to or None, limit=max(1, min(limit, 5000)))
    for e in entries:
        e["kind_ru"], e["kind_zh"] = journal.kind_label(e.get("kind", "")), journal.kind_label(e.get("kind", ""), "zh")
    return {"entries": entries}


@app.get("/api/journal/kinds")
def journal_kinds():
    return [{"kind": k, "ru": v[0], "zh": v[1]} for k, v in journal.KINDS.items()]


@app.get("/api/journal/days")
def journal_days(month: str = ""):
    """month = 'YYYY-MM' -> {'2026-10-01': 3, ...} для календаря."""
    return journal.days_in_month(month)


@app.get("/api/journal/export")
def journal_export(request: Request, day: str = "", q: str = "", kind: str = "", date_from: str = "",
                   date_to: str = ""):
    data = journal.export_csv(day=day or None, q=q or None, kind=kind or None, date_from=date_from or None,
                              date_to=date_to or None)
    journal.log("export", _user(request), "выгрузка журнала в CSV",
                meta={"day": day, "q": q, "kind": kind, "from": date_from, "to": date_to})
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="sibdoc_journal.csv"'})


# ------------------------------------------------------------------ курсы валют
class ManualRateBody(BaseModel):
    base: str
    quote: str
    value: float
    date: Optional[str] = None
    month: Optional[str] = None
    source_scope: str = "any"
    comment: str = ""


class RefreshBody(BaseModel):
    source: str = "cbr"
    date: str


def _rates():
    from app import rates
    return rates


@app.get("/api/rates")
def rates_list(date: str, source: str = ""):
    r = _rates()
    try:
        return {"date": date, "rates": r.list_rates(date, source or None), "settings": r.load_settings()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@app.post("/api/rates/refresh")
def rates_refresh(body: RefreshBody, request: Request):
    r = _rates()
    try:
        got = r.refresh(body.source, body.date, user=_user(request))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Курс не получен: {exc}")
    return {"source": body.source, "date": body.date, "rates": got}


@app.get("/api/rates/convert")
def rates_convert(amount: float, from_ccy: str, to_ccy: str, date: str, side: str = "ru", source: str = ""):
    r = _rates()
    try:
        val, info = r.convert(amount, from_ccy, to_ccy, date, side=side, source=source or None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))
    return {"value": val, "rate": info.as_dict()}


@app.get("/api/rates/manual")
def rates_manual_list():
    return _rates().list_manual()


@app.post("/api/rates/manual")
def rates_manual_add(body: ManualRateBody, request: Request):
    try:
        return _rates().set_manual(body.base, body.quote, body.value, date=body.date or None,
                                   month=body.month or None, source_scope=body.source_scope,
                                   comment=body.comment, user=_user(request))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@app.delete("/api/rates/manual/{entry_id}")
def rates_manual_del(entry_id: str, request: Request):
    if not _rates().delete_manual(entry_id, user=_user(request)):
        raise HTTPException(404, "запись не найдена")
    return {"deleted": entry_id}


@app.get("/api/rates/settings")
def rates_settings():
    return _rates().load_settings()


@app.put("/api/rates/settings")
def rates_settings_put(body: dict, request: Request):
    try:
        _rates().save_settings(body, user=_user(request))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))
    return _rates().load_settings()


# ------------------------------------------------------------------ коннекторы и уведомления
class ReloadBody(BaseModel):
    pull: bool = True   # заодно опросить включённые коннекторы


def _connectors():
    from app import connectors
    return connectors


def _call(fn, *args, user="-"):
    """Вызов метода коннектора с передачей пользователя, если метод его принимает."""
    try:
        return fn(*args, user=user)
    except TypeError:
        return fn(*args)


@app.get("/api/connectors")
def connectors_list():
    c = _connectors()
    return [x.public_dict() for x in c.list_connectors(_cfg())]


@app.get("/api/connectors/catalog")
def connectors_catalog():
    c = _connectors()
    return c.catalog() if hasattr(c, "catalog") else []


@app.post("/api/connectors/{cid}/test")
def connectors_test(request: Request, cid: str):
    conn = _connectors().get_connector(_cfg(), cid)
    if conn is None:
        raise HTTPException(404, "коннектор не найден")
    res = _call(conn.test, user=_user(request))
    return res


def _ingest_pulled(got: list[dict], cfg: dict) -> list[str]:
    """Файлы, забранные коннектором, -> слоты (по слоту из коннектора или по имени файла)."""
    by_slot: dict[str, list[tuple[bytes, str]]] = {}
    for item in got:
        slot = item.get("slot") or _slot_for_filename(item.get("file") or item.get("path", ""), cfg)
        if not slot or slot not in cfg.get("sources", {}):
            continue
        with open(item["path"], "rb") as fh:
            by_slot.setdefault(slot, []).append((fh.read(), os.path.basename(item.get("file") or item["path"])))
    done = []
    for slot, files in by_slot.items():
        if not cfg["sources"][slot].get("multi"):
            files = files[-1:]
        _ingest(slot, files, cfg)
        _store_files(slot, files, append=False)
        done.append(slot)
    return done


@app.post("/api/connectors/{cid}/pull")
def connectors_pull(request: Request, cid: str):
    cfg = _cfg()
    conn = _connectors().get_connector(cfg, cid)
    if conn is None:
        raise HTTPException(404, "коннектор не найден")
    tmp = os.path.join(STORAGE_DIR, "connectors", "incoming", cid)
    os.makedirs(tmp, exist_ok=True)
    try:
        got = _call(conn.pull, tmp, user=_user(request))
        slots = _ingest_pulled(got, cfg)
    except Exception as exc:  # noqa: BLE001
        _notify(cfg, "connector_error", f"SibDoc: коннектор {cid} — {exc}", user=_user(request))
        raise HTTPException(400, str(exc))
    journal.log("pull", _user(request), f"{cid}: {len(got)} файлов, в источники: {', '.join(slots) or '-'}")
    return {"pulled": got, "slots": slots}


@app.post("/api/reload")
def reload_data(request: Request, body: "Optional[ReloadBody]" = None):
    """Перечитать сохранённые файлы и (опционально) опросить включённые коннекторы."""
    body = body or ReloadBody()
    cfg = _cfg()
    pulled, errors, slots = [], [], []
    if body.pull:
        for conn in _connectors().list_connectors(cfg):
            if not conn.enabled:
                continue
            tmp = os.path.join(STORAGE_DIR, "connectors", "incoming", conn.id)
            os.makedirs(tmp, exist_ok=True)
            try:
                got = _call(conn.pull, tmp, user=_user(request))
                pulled.extend({**item, "connector": conn.id} for item in got)
                slots += _ingest_pulled(got, cfg)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{conn.id}: {exc}")
    restored = _restore_uploads()
    journal.log("reload", _user(request), f"перечитано источников: {len(restored)}"
                + (f", из коннекторов: {len(pulled)} файлов" if pulled else "")
                + (f", ошибок: {len(errors)}" if errors else ""))
    return {"restored": restored, "pulled": pulled, "errors": errors, "sources": sources_state()}


@app.get("/api/notifications")
def notifications_list():
    cfg = _cfg()
    out = []
    for cid, ch in (cfg.get("notifications") or {}).items():
        if isinstance(ch, dict) and "type" in ch:
            out.append({"id": cid, "type": ch.get("type"), "label": ch.get("label", cid),
                        "enabled": bool(ch.get("enabled")), "events": ch.get("events") or []})
    return out


@app.post("/api/notifications/{channel}/test")
def notifications_test(channel: str, request: Request):
    try:
        from app import notify
        return notify.test_channel(_cfg(), channel, user=_user(request))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


def _notify(cfg: dict, event: str, text: str, files: list[str] | None = None, user: str = "system") -> list[dict]:
    try:
        from app import notify
        return notify.send(cfg, event, text, files, user=user)
    except Exception as exc:  # noqa: BLE001
        return [{"ok": False, "error": str(exc)}]


# ------------------------------------------------------------------ scans (OCR)
SCAN_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")


@app.get("/api/scans/info")
def scans_info():
    return {"ocr_langs": available_languages(), "tesseract": tesseract_available(),
            "accepted": list(SCAN_EXTS), "loaded": len(_scans)}


@app.post("/api/scans/upload")
async def upload_scans(request: Request, files: list[UploadFile] = File(...)):
    """Массовая загрузка сканов деклараций: текстовый слой PDF или OCR (ru+en+zh) -> извлечение полей."""
    limit = _max_bytes(_cfg())
    results = []
    for f in files:
        if not f.filename.lower().endswith(SCAN_EXTS):
            results.append({"file": f.filename, "ok": False,
                            "error": f"Формат не поддерживается ({', '.join(SCAN_EXTS)})"})
            continue
        try:
            content = await _read_limited(f, limit)
            ocr = read_scan(io.BytesIO(content), f.filename)
            parsed = parse_declaration(ocr["text"])
            rec = {"file": f.filename, "ok": True, "pages": ocr["pages"], "langs": ocr["engine_langs"],
                   "text_layer_pages": ocr.get("text_layer_pages", 0), "text_excerpt": ocr["text"][:600],
                   "has_cjk": bool(re.search(r"[一-鿿]", ocr["text"])),
                   "has_cyr": bool(re.search(r"[А-Яа-яЁё]", ocr["text"])), "fields": parsed}
            _scans.append(rec)
            results.append(rec)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            results.append({"file": f.filename, "ok": False, "error": str(exc)})
    ok_n = sum(1 for r in results if r.get("ok"))
    journal.log("ocr", _user(request), f"сканов: {len(results)}, распознано: {ok_n}",
                meta={"files": [r["file"] for r in results]})
    return {"processed": len(results), "ok": ok_n, "results": results}


@app.get("/api/scans/preview")
def scans_preview(lang: str = "ru"):
    rows = build_preview_rows(_scans)
    labels_ = {}
    if _scans:
        for item in fields_summary(_scans[0]["fields"], lang=lang):
            labels_[item["field"]] = item["label"]
    return {"count": len(rows), "labels": labels_, "rows": rows}


@app.delete("/api/scans")
def clear_scans():
    _scans.clear()
    return {"cleared": True}


@app.get("/api/scans/excel")
def scans_excel():
    if not _scans:
        raise HTTPException(404, "Сначала загрузите сканы (/api/scans/upload)")
    path = os.path.join(REPORTS_DIR, "scan_preview.xlsx")
    write_preview_excel(_scans, path)
    return FileResponse(path, filename="scan_preview.xlsx")


@app.post("/api/scans/to-source")
def scans_to_source(request: Request):
    """Распознанные сканы -> строки источников ru_dt (ДТ РФ) и cn_dec (报关单): участвуют в сверке."""
    if not _scans:
        raise HTTPException(404, "Нет распознанных сканов")
    cfg = _cfg()
    rows = {"ru_dt": [], "cn_dec": []}
    for s in _scans:
        f = {k: (v.get("value") or "") for k, v in s["fields"].items()}
        common = {"container": f.get("container_num"), "contract": f.get("contract"),
                  "incoterms": f.get("incoterms"), "hs_code": f.get("hs_code"), "net_weight": f.get("net_weight"),
                  "model": f.get("declared_model"), "source_file": f"{s['file']} (скан)"}
        if f.get("decl_ru") or (s.get("has_cyr") and not s.get("has_cjk")):
            rows["ru_dt"].append({"decl_ru": f.get("decl_ru"), **common})
        if f.get("decl_num") or s.get("has_cjk"):
            rows["cn_dec"].append({"decl_cn": f.get("decl_num"), "unit_price": f.get("unit_price"), **common})
    added = {}
    for slot, rr in rows.items():
        if not rr:
            continue
        new = pd.DataFrame(rr)
        cur = _uploads.get(slot)
        df = new if cur is None else pd.concat([cur.df, new], ignore_index=True, sort=False)
        info = dict(cur.info) if cur is not None else {"files": []}
        info["scans"] = info.get("scans", 0) + len(rr)
        _uploads[slot] = Source(slot=slot, name=(cur.name if cur else "сканы"), df=df, info=info)
        added[slot] = len(rr)
    journal.log("scan_to_source", _user(request),
                ", ".join(f"{k}: +{v}" for k, v in added.items()) or "нечего добавить", meta=added)
    return {"added": added}


# ------------------------------------------------------------------ run
@app.post("/api/run")
def run_checks(request: Request):
    cfg = _cfg()
    sources = _all_sources(cfg)
    result = engine_run(sources, cfg, user=_user(request))
    files = runs.save(result, cfg)
    _last_result["run"], _last_result["files"] = result, files
    summary = {row["check_id"]: row for row in result.summary_rows()}
    n_issue = sum(int(r.get("ISSUE", 0)) for r in summary.values())
    n_review = sum(int(r.get("REVIEW", 0)) for r in summary.values())
    journal.log("run", _user(request),
                f"прогон {result.run_id}: ISSUE {n_issue}, REVIEW {n_review}, "
                f"ошибок данных {result.dq_counts().get('ERROR', 0)}",
                meta={"run_id": result.run_id, "summary": result.summary_rows(),
                      "sources": [s["slot"] for s in result.sources_info]})
    journal.log("report", _user(request), f"отчёты прогона {result.run_id}: Excel и PDF",
                meta={"run_id": result.run_id, "excel": os.path.basename(files["excel"]),
                      "pdf": os.path.basename(files["pdf"])})
    text = (f"SibDoc: прогон {result.run_id} — расхождений {n_issue}, на проверку {n_review}, "
            f"ошибок данных {result.dq_counts().get('ERROR', 0)}")
    notified = _notify(cfg, "issues_found" if n_issue else "run_done", text, [files["excel"], files["pdf"]],
                       user=_user(request))
    return {
        "run_id": result.run_id, "created_at": result.created_at,
        "summary": result.summary_rows(),
        "sources": result.sources_info,
        "data_quality": [f.as_dict() for f in result.findings],
        "dq_counts": result.dq_counts(),
        "total_issues": result.total_issues(),
        "duration_sec": result.duration_sec,
        "rates_used": result.rates_used,
        "checks": {cid: {"title": labels.check_title(cid), "title_zh": labels.check_title(cid, "zh"),
                         "counts": out.counts(), "meta": {k: v for k, v in out.meta.items() if k != "traceback"},
                         "columns": [{"key": c, "label_ru": labels.column(c, "ru"), "label_zh": labels.column(c, "zh")}
                                     for c in out.df.columns if c != "check"],
                         "rows": _jsonable(out.df.drop(columns=[c for c in ("check",) if c in out.df.columns]))}
                   for cid, out in result.outputs.items()},
        "reports": {"excel": f"/api/runs/{result.run_id}/excel", "pdf": f"/api/runs/{result.run_id}/pdf"},
        "notified": notified,
    }


@app.get("/api/runs")
def runs_list(limit: int = 50):
    return runs.list_runs(limit)


@app.get("/api/runs/{run_id}/{kind}")
def run_report(run_id: str, kind: str):
    path = runs.report_path(run_id, kind)
    if not path:
        raise HTTPException(404, "Отчёт не найден")
    return FileResponse(path, filename=os.path.basename(path))


@app.get("/api/report/excel")
def report_excel():
    path = os.path.join(PROJECT_ROOT, _cfg()["output"]["dir"], _cfg()["output"]["excel_name"])
    if not os.path.exists(path):
        raise HTTPException(404, "Отчёт ещё не сформирован — запустите /api/run")
    return FileResponse(path, filename="post_control_report.xlsx")


@app.get("/api/report/pdf")
def report_pdf():
    path = os.path.join(PROJECT_ROOT, _cfg()["output"]["dir"], _cfg()["output"]["pdf_name"])
    if not os.path.exists(path):
        raise HTTPException(404, "Отчёт ещё не сформирован — запустите /api/run")
    return FileResponse(path, filename="post_control_report.pdf")


# ------------------------------------------------------------------ static UI
static_dir = os.path.join(PROJECT_ROOT, "webui")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="webui")


# ------------------------------------------------------------------ startup
if security.ensure_admin():
    journal.log("user_add", "system", "создан пользователь admin с паролем по умолчанию — смените его "
                                      "при включении входа (auth.disabled: false)")
_migrate_legacy()
_restored = _restore_uploads()
if _restored:
    journal.log("reload", "system", f"восстановлены источники: {', '.join(_restored)}")
