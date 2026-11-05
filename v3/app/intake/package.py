from __future__ import annotations
# -*- coding: utf-8 -*-
"""Пакет документов: хранение и обработка.

storage/packages/<id>/
    meta.json            — режим («ретро» / «реальное время»), время, файлы, список документов, прогресс
    originals/           — загруженные файлы как есть
    parts/               — файлы, извлечённые из архивов и писем
    pages/               — картинки страниц для просмотра
    docs/<doc>.json      — документ: страницы / листы (текст, таблицы), тип, поля, товары, источники
    Пакет.xlsx           — все документы одной книгой (app/intake/excel.py)
    realtime/            — результаты проверки отгрузки (app/realtime)

Обработка: чтение форматов (formats) -> деление на документы, тип, поля
(pipeline) -> Excel. Может идти в фоне: прогресс — в meta.json → progress.
"""
import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime

from app.intake import classify as cls_mod
from app.intake import extract as ex
from app.intake import formats, pipeline

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PACKAGES_DIR = os.path.join(PROJECT_ROOT, "storage", "packages")
EXCEL_NAME = "Пакет.xlsx"
MODES = {"retro": "ретро", "realtime": "реальное время"}
_ID_RX = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_DOC_RX = re.compile(r"^d\d{3,5}$")
_LOCKS: dict[str, threading.RLock] = {}
_GLOBAL = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


class PackageError(ValueError):
    pass


class NotFound(PackageError):
    pass


# ------------------------------------------------------------------ файлы
def _lock(pid: str) -> threading.RLock:
    with _GLOBAL:
        return _LOCKS.setdefault(pid, threading.RLock())


def pkg_dir(pid: str) -> str:
    if not _ID_RX.match(pid or ""):
        raise NotFound(f"нет пакета {pid!r}")
    d = os.path.join(PACKAGES_DIR, pid)
    if not os.path.isdir(d):
        raise NotFound(f"нет пакета {pid}")
    return d


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def safe_name(name: str) -> str:
    base = os.path.basename((name or "file").replace("\\", "/"))
    return re.sub(r"[\x00-\x1f<>:\"|?*\\/]", "_", base)[:150] or "file"


def _unique(dirpath: str, name: str) -> str:
    stem, ext = os.path.splitext(safe_name(name))
    cand, n = stem + ext, 1
    while os.path.exists(os.path.join(dirpath, cand)):
        n += 1
        cand = f"{stem} ({n}){ext}"
    return cand


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ пакет
def create(mode: str = "realtime", title: str = "", user: str = "-") -> dict:
    if mode not in MODES:
        raise PackageError(f"режим должен быть одним из: {', '.join(MODES)}")
    pid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = os.path.join(PACKAGES_DIR, pid)
    for sub in ("originals", "parts", "pages", "docs"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    meta = {"id": pid, "mode": mode, "mode_label": MODES[mode], "title": title or "", "created": _now(),
            "updated": _now(), "user": user, "status": "new", "message": "",
            "progress": {"stage": "создан", "done": 0, "total": 0, "current": ""},
            "files": [], "documents": [], "counts": {}, "next_doc": 1, "next_part": 1, "warnings": []}
    _write_json(os.path.join(d, "meta.json"), meta)
    return meta


def load_meta(pid: str) -> dict:
    return _read_json(os.path.join(pkg_dir(pid), "meta.json"))


def save_meta(pid: str, meta: dict) -> None:
    meta["updated"] = _now()
    _write_json(os.path.join(pkg_dir(pid), "meta.json"), meta)


def list_packages() -> list[dict]:
    if not os.path.isdir(PACKAGES_DIR):
        return []
    out = []
    for pid in os.listdir(PACKAGES_DIR):
        if not _ID_RX.match(pid):
            continue
        try:
            m = load_meta(pid)
        except (OSError, ValueError, NotFound):
            continue
        out.append({k: m.get(k) for k in ("id", "mode", "mode_label", "title", "created", "updated", "status",
                                          "counts", "user")} | {"documents": len(m.get("documents") or []),
                                                                 "files": len(m.get("files") or [])})
    return sorted(out, key=lambda x: x["created"] or "", reverse=True)


def delete(pid: str) -> None:
    d = pkg_dir(pid)
    t = _THREADS.get(pid)
    if t is not None and t.is_alive():
        raise PackageError("пакет ещё обрабатывается — удалить можно после окончания")
    shutil.rmtree(d, ignore_errors=True)
    _LOCKS.pop(pid, None)


def load_doc(pid: str, doc_id: str) -> dict:
    if not _DOC_RX.match(doc_id or ""):
        raise NotFound(f"нет документа {doc_id!r}")
    path = os.path.join(pkg_dir(pid), "docs", f"{doc_id}.json")
    if not os.path.exists(path):
        raise NotFound(f"нет документа {doc_id} в пакете {pid}")
    return _read_json(path)


def save_doc(pid: str, doc: dict) -> None:
    _write_json(os.path.join(pkg_dir(pid), "docs", f"{doc['id']}.json"), doc)


def documents(pid: str) -> list[dict]:
    """Полные документы пакета (в порядке списка)."""
    meta = load_meta(pid)
    out = []
    for s in meta.get("documents") or []:
        try:
            out.append(load_doc(pid, s["id"]))
        except (NotFound, OSError, ValueError):
            continue
    return out


def summary(doc: dict) -> dict:
    return {"id": doc["id"], "file": doc["file"], "name": doc.get("name"), "container": doc.get("container", ""),
            "format": doc.get("format"), "pages": doc.get("pages"), "sheets": doc.get("sheets") or [],
            "type": doc["type"], "type_label": cls_mod.type_label(doc["type"]),
            "type_label_zh": cls_mod.type_label(doc["type"], "zh"), "type_auto": doc.get("type_auto"),
            "type_source": doc.get("type_source"), "confidence": doc.get("confidence"),
            "status": doc.get("status"), "status_reason": doc.get("status_reason", ""),
            "doc_number": doc.get("doc_number", ""), "fields": len(doc.get("fields") or {}),
            "items": len(doc.get("items") or []), "review_fields": len(pipeline.low_conf_fields(doc)),
            "split": doc.get("split") or {}, "warnings": doc.get("warnings") or []}


def _counts(docs_summary: list) -> dict:
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for d in docs_summary:
        by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        by_type[d["type"]] = by_type.get(d["type"], 0) + 1
    return {"total": len(docs_summary), "by_status": by_status, "by_type": by_type}


# ------------------------------------------------------------------ обработка
def add_files(pid: str, files: list, user: str = "-", background: bool = False) -> dict:
    """Добавить файлы [(байты, имя)] в пакет и разобрать их (сразу или в фоне)."""
    d = pkg_dir(pid)
    if not files:
        raise PackageError("не передано ни одного файла")
    with _lock(pid):
        t = _THREADS.get(pid)
        if t is not None and t.is_alive():
            raise PackageError("пакет ещё обрабатывается — дождитесь окончания")
        meta = load_meta(pid)
        stored = []
        for data, name in files:
            fname = _unique(os.path.join(d, "originals"), name)
            with open(os.path.join(d, "originals", fname), "wb") as fh:
                fh.write(data)
            meta["files"].append({"name": safe_name(name), "stored": f"originals/{fname}", "size": len(data),
                                  "added": _now(), "user": user})
            stored.append((f"originals/{fname}", safe_name(name)))
        meta["status"] = "processing"
        meta["progress"] = {"stage": "чтение файлов", "done": 0, "total": len(stored), "current": "",
                            "started": _now()}
        save_meta(pid, meta)
    if background:
        th = threading.Thread(target=_process, args=(pid, stored, user), daemon=True, name=f"intake-{pid}")
        _THREADS[pid] = th
        th.start()
        return load_meta(pid)
    _process(pid, stored, user)
    return load_meta(pid)


def wait(pid: str, timeout: float = 600) -> dict:
    t = _THREADS.get(pid)
    if t is not None:
        t.join(timeout)
    return load_meta(pid)


def _process(pid: str, stored: list, user: str) -> None:
    d = pkg_dir(pid)
    t0 = time.time()
    try:
        with _lock(pid):
            meta = load_meta(pid)
        lim = (ex.config().get("limits") or {})

        def preview(part_no: int, page_no: int, img) -> str:
            rel = f"pages/p{part_no:04d}_{page_no:03d}.jpg"
            im = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            if im.width > 1100:
                im = im.resize((1100, int(im.height * 1100 / im.width)))
            im.save(os.path.join(d, rel), "JPEG", quality=70, optimize=True)
            return rel

        def progress(path: str) -> None:
            with _lock(pid):
                m = load_meta(pid)
                m["progress"]["current"] = path
                save_meta(pid, m)

        ctx = formats.ReadContext(lim, preview=preview, progress=progress)
        ctx.parts = int(meta.get("next_part", 1)) - 1
        counter = {"n": int(meta.get("next_doc", 1))}

        def next_id() -> str:
            n = counter["n"]
            counter["n"] += 1
            return f"d{n:03d}"

        new_docs: list[dict] = []
        for k, (rel, name) in enumerate(stored, start=1):
            with open(os.path.join(d, rel), "rb") as fh:
                data = fh.read()
            parts = formats.read_any(data, name, ctx)
            with _lock(pid):
                m = load_meta(pid)
                m["progress"].update(stage="разбор документов", done=k - 1, current=name)
                save_meta(pid, m)
            for part in parts:
                if part.container:
                    if part.data:
                        pname = f"{part.no:04d}_{safe_name(part.name)}"
                        with open(os.path.join(d, "parts", pname), "wb") as fh:
                            fh.write(part.data)
                        part.stored = f"parts/{pname}"
                else:
                    part.stored = rel
                for doc in pipeline.documents_from_part(part, next_id):
                    save_doc(pid, doc)
                    new_docs.append(summary(doc))
            with _lock(pid):
                m = load_meta(pid)
                m["documents"] = list(m.get("documents") or []) + new_docs
                new_docs = []
                m["progress"].update(done=k, documents=len(m["documents"]))
                m["next_doc"] = counter["n"]
                m["next_part"] = ctx.parts + 1
                save_meta(pid, m)
        with _lock(pid):
            m = load_meta(pid)
            m["progress"].update(stage="Excel «Пакет.xlsx»", current="")
            save_meta(pid, m)
        rebuild_excel(pid)
        with _lock(pid):
            m = load_meta(pid)
            m["counts"] = _counts(m["documents"])
            m["warnings"] = list(dict.fromkeys((m.get("warnings") or []) + ctx.warnings))
            m["status"] = "done"
            m["progress"].update(stage="готово", current="", seconds=round(time.time() - t0, 1),
                                 ocr_pages=ctx.ocr_pages)
            save_meta(pid, m)
    except Exception as exc:  # noqa: BLE001 — ошибка в пакете не должна ронять сервер
        try:
            with _lock(pid):
                m = load_meta(pid)
                m["status"] = "error"
                m["message"] = f"{exc}"
                m["traceback"] = traceback.format_exc()[-2000:]
                save_meta(pid, m)
        except Exception:  # noqa: BLE001
            pass


def rebuild_excel(pid: str) -> str:
    from app.intake.excel import write_package_excel
    d = pkg_dir(pid)
    return write_package_excel(load_meta(pid), documents(pid), os.path.join(d, EXCEL_NAME))


def excel_path(pid: str) -> str:
    path = os.path.join(pkg_dir(pid), EXCEL_NAME)
    if not os.path.exists(path):
        rebuild_excel(pid)
    return path


def _part_bytes(pid: str, doc: dict) -> bytes:
    rel = doc.get("stored") or ""
    path = os.path.join(pkg_dir(pid), rel)
    if rel and os.path.isfile(path) and os.path.realpath(path).startswith(os.path.realpath(pkg_dir(pid))):
        with open(path, "rb") as fh:
            return fh.read()
    return b""


def set_type(pid: str, doc_id: str, new_type: str, user: str = "-") -> dict:
    """Тип документа вручную (или «auto» — вернуть автоматический) и повторное извлечение полей."""
    _, rules = cls_mod.config()
    if new_type != "auto" and new_type not in rules:
        raise PackageError(f"неизвестный тип {new_type!r}; допустимо: {', '.join(rules)} или auto")
    with _lock(pid):
        doc = load_doc(pid, doc_id)
        if new_type == "auto":
            doc["type"], doc["type_source"] = doc.get("type_auto", "other"), "auto"
            doc["confidence"] = (doc.get("classification") or {}).get("confidence", 0.0)
        else:
            doc["type"], doc["type_source"], doc["confidence"] = new_type, "manual", 1.0
        doc.setdefault("history", []).append({"at": _now(), "user": user, "type": doc["type"],
                                              "source": doc["type_source"]})
        # байты файла: XML — для профилей деклараций; весь файл (не часть) — для разборщиков слотов
        # (пакет ВЭД, выписки банка, реестр договоров: app/intake/plugins.py)
        data = _part_bytes(pid, doc) if doc.get("format") == "xml" or not doc.get("split") else b""
        pipeline.run_extraction(doc, data)
        save_doc(pid, doc)
        meta = load_meta(pid)
        meta["documents"] = [summary(doc) if s["id"] == doc_id else s for s in meta.get("documents") or []]
        meta["counts"] = _counts(meta["documents"])
        save_meta(pid, meta)
    rebuild_excel(pid)
    return doc


def original_path(pid: str, doc_id: str) -> tuple[str, str]:
    doc = load_doc(pid, doc_id)
    path = os.path.join(pkg_dir(pid), doc.get("stored") or "")
    if not doc.get("stored") or not os.path.isfile(path):
        raise NotFound("файл документа не сохранён")
    return path, doc.get("name") or os.path.basename(path)


def page_image(pid: str, doc_id: str, page: int) -> str:
    doc = load_doc(pid, doc_id)
    for u in doc.get("units") or []:
        if int(u.get("index", 0)) == int(page) and u.get("image"):
            path = os.path.join(pkg_dir(pid), u["image"])
            if os.path.isfile(path):
                return path
    raise NotFound("нет картинки этой страницы")


def realtime_dir(pid: str) -> str:
    d = os.path.join(pkg_dir(pid), "realtime")
    os.makedirs(d, exist_ok=True)
    return d
