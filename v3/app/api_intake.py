# -*- coding: utf-8 -*-
"""SibDoc 3: API пакетной загрузки и проверки отгрузки в реальном времени.

Подключается в app/server.py одной строкой (include_router). Описание для
интерфейса — docs/REALTIME.md, раздел «API».

  GET    /api/intake/info                                   форматы, OCR, LibreOffice, типы, разборщики
  GET    /api/intake/packages                               список пакетов
  POST   /api/intake/packages                               новый пакет: files[] (multipart), mode, title; ?wait=
  POST   /api/intake/packages/{id}/files                    добавить файлы в пакет
  GET    /api/intake/packages/{id}                          пакет: режим, файлы, документы, счётчики
  GET    /api/intake/packages/{id}/status                   прогресс обработки
  DELETE /api/intake/packages/{id}                          удалить пакет
  GET    /api/intake/packages/{id}/documents                документы с типами (?type=&status=)
  GET    /api/intake/packages/{id}/documents/{doc}          документ: поля с источниками, товары, текст
  PUT    /api/intake/packages/{id}/documents/{doc}/type     {"type": "invoice" | … | "auto"} + повторное извлечение
  GET    /api/intake/packages/{id}/documents/{doc}/original исходный файл
  GET    /api/intake/packages/{id}/documents/{doc}/pages/{n} картинка страницы
  GET    /api/intake/packages/{id}/excel                    «Пакет.xlsx»
  POST   /api/intake/packages/{id}/to-retro                 «добавить в сверку»: ДТ, 报关单, SAP, черновики, пакет ВЭД,
                                                            выписки банка, договоры -> слоты ретро
  POST   /api/realtime/{id}/check                           проверка отгрузки
  GET    /api/realtime/{id}/result                          последний результат (JSON)
  GET    /api/realtime/{id}/excel                           «Проверка отгрузки.xlsx»
  GET/PUT /api/realtime/rules                               правила config/rules/realtime.yaml
"""
from __future__ import annotations

from typing import Optional, Union
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import journal
from app.intake import classify as cls_mod
from app.intake import extract as ex
from app.intake import formats
from app.intake import package as pkg

router = APIRouter(prefix="/api", tags=["SibDoc 3: пакеты и реальное время"])

journal.KINDS.setdefault("intake", ("Пакет документов", "文件包"))
journal.KINDS.setdefault("intake_type", ("Пакет: тип документа вручную", "文件包：手动指定类型"))
journal.KINDS.setdefault("intake_to_retro", ("Пакет → сверка (ретро)", "文件包→核对"))
journal.KINDS.setdefault("realtime_check", ("Проверка отгрузки", "发货核查"))

RETRO_SLOTS = {"ru_dt": "ru_dt", "cn_dec": "cn_dec", "system_export": "purchase", "draft": "cd_drafts",
               # SibDoc3: пакет ВЭД и выписки банка / реестр договоров (ветки 2.1 и 2.2)
               "invoice": "invoice", "packing": "packing", "transport": "transport", "conformity": "conformity",
               "bank": "bank", "contract": "contract_register"}
# Целый исходный файл (а не извлечённые строки) уходит в слот, если слот читает такой формат сам:
# Excel / CSV / XML — все; выписки банка — ещё TXT (1С «Клиент-банк», MT940).
WHOLE_FORMATS = {"default": ("xml", "excel", "csv"), "bank": ("xml", "excel", "csv", "txt")}


def _srv():
    from app import server
    return server


def _user(request: Request) -> str:
    return getattr(request.state, "user", "-")


def _err(exc: Exception):
    if isinstance(exc, pkg.NotFound):
        raise HTTPException(404, str(exc))
    if isinstance(exc, pkg.PackageError):
        raise HTTPException(400, str(exc))
    raise exc


def _download(path: str, name: str) -> FileResponse:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
    return FileResponse(path, filename=ascii_name,
                        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                                        f"filename*=UTF-8''{quote(name)}"})


# ------------------------------------------------------------------ сведения
@router.get("/intake/info")
def intake_info():
    lim = ex.config().get("limits") or {}
    return {"formats": {"архивы": [".zip (вложенные папки и архивы)"],
                        "PDF": [".pdf (текстовый слой или OCR, несколько документов в одном файле делятся)"],
                        "Excel": [".xlsx", ".xlsm", ".xls", ".csv", ".tsv"],
                        "Word": [".docx", ".doc / .rtf / .odt / .ods — через LibreOffice, если установлен"],
                        "картинки": [".jpg", ".jpeg", ".png", ".tif/.tiff (многостраничный)", ".bmp", ".gif", ".webp"],
                        "прочее": [".xml", ".txt", ".html", ".eml (письмо с вложениями)"]},
            "ocr": {"tesseract": formats.ocr_available(), "langs": formats.ocr_reader.available_languages()},
            "libreoffice": bool(formats.libreoffice_cmd()), "types": cls_mod.types_public(),
            "extractors": ex.extractors(), "limits": lim, "modes": pkg.MODES,
            "statuses": ["распознан", "требует проверки", "не распознан", "ошибка"]}


# ------------------------------------------------------------------ пакеты
async def _read_files(files: list[UploadFile]) -> list[tuple[bytes, str]]:
    srv = _srv()
    limit = srv._max_bytes(srv._cfg())
    out = []
    for f in files or []:
        if f is None or not f.filename:
            continue
        out.append((await srv._read_limited(f, limit), f.filename))
    if not out:
        raise HTTPException(400, "Не передано ни одного файла (поле files)")
    return out


@router.get("/intake/packages")
def packages_list():
    return pkg.list_packages()


@router.post("/intake/packages")
async def packages_create(request: Request, files: list[UploadFile] = File(...), mode: str = Form("realtime"),
                          title: str = Form(""), wait: bool = False):
    payload = await _read_files(files)
    try:
        meta = pkg.create(mode=mode, title=title, user=_user(request))
        meta = await run_in_threadpool(pkg.add_files, meta["id"], payload, _user(request), not wait)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    journal.log("intake", _user(request), f"пакет {meta['id']} ({meta['mode_label']}): файлов {len(payload)}"
                + (f", документов {len(meta['documents'])}" if wait else ", обработка в фоне"),
                meta={"package": meta["id"], "files": [n for _, n in payload][:50]})
    return JSONResponse(meta, status_code=200 if wait else 202)


@router.post("/intake/packages/{pid}/files")
async def packages_add(pid: str, request: Request, files: list[UploadFile] = File(...), wait: bool = False):
    payload = await _read_files(files)
    try:
        meta = await run_in_threadpool(pkg.add_files, pid, payload, _user(request), not wait)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    journal.log("intake", _user(request), f"пакет {pid}: добавлено файлов {len(payload)}",
                meta={"package": pid, "files": [n for _, n in payload][:50]})
    return JSONResponse(meta, status_code=200 if wait else 202)


@router.get("/intake/packages/{pid}")
def packages_get(pid: str):
    try:
        return pkg.load_meta(pid)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


@router.get("/intake/packages/{pid}/status")
def packages_status(pid: str):
    try:
        m = pkg.load_meta(pid)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    return {"id": pid, "status": m["status"], "message": m.get("message", ""), "progress": m.get("progress"),
            "documents": len(m.get("documents") or []), "counts": m.get("counts")}


@router.delete("/intake/packages/{pid}")
def packages_delete(pid: str, request: Request):
    try:
        pkg.delete(pid)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    journal.log("intake", _user(request), f"пакет {pid} удалён", meta={"package": pid})
    return {"id": pid, "deleted": True}


@router.get("/intake/packages/{pid}/documents")
def documents_list(pid: str, type: str = "", status: str = ""):
    try:
        docs = pkg.load_meta(pid).get("documents") or []
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    return [d for d in docs if (not type or d["type"] == type) and (not status or d["status"] == status)]


@router.get("/intake/packages/{pid}/documents/{doc_id}")
def documents_get(pid: str, doc_id: str, units: bool = False):
    try:
        d = pkg.load_doc(pid, doc_id)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    out = {k: v for k, v in d.items() if k != "units"}
    out["type_label"] = cls_mod.type_label(d["type"])
    out["type_label_zh"] = cls_mod.type_label(d["type"], "zh")
    out["text"] = "\n\n".join("\n".join(t for t, _ in u.get("lines") or []) for u in d.get("units") or [])[:20000]
    out["pages_info"] = [{"index": u.get("index"), "label": u.get("label"), "method": u.get("method"),
                          "ocr_conf": u.get("ocr_conf"), "image": bool(u.get("image")),
                          "image_url": f"/api/intake/packages/{pid}/documents/{doc_id}/pages/{u.get('index')}"
                          if u.get("image") else None, "warnings": u.get("warnings") or []}
                         for u in d.get("units") or []]
    out["original_url"] = f"/api/intake/packages/{pid}/documents/{doc_id}/original"
    if units:
        out["units"] = d.get("units")
    return out


class TypeBody(BaseModel):
    type: str


@router.put("/intake/packages/{pid}/documents/{doc_id}/type")
def documents_set_type(pid: str, doc_id: str, body: TypeBody, request: Request):
    try:
        before = pkg.load_doc(pid, doc_id)["type"]
        d = pkg.set_type(pid, doc_id, body.type, user=_user(request))
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    journal.log("intake_type", _user(request), f"пакет {pid}, {doc_id}: тип {before} → {d['type']}"
                + (" (автоматически)" if body.type == "auto" else ""),
                meta={"package": pid, "doc": doc_id, "from": before, "to": d["type"]})
    return {k: v for k, v in d.items() if k != "units"} | {"type_label": cls_mod.type_label(d["type"])}


@router.get("/intake/packages/{pid}/documents/{doc_id}/original")
def documents_original(pid: str, doc_id: str):
    try:
        path, name = pkg.original_path(pid, doc_id)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    return _download(path, name)


@router.get("/intake/packages/{pid}/documents/{doc_id}/pages/{page}")
def documents_page(pid: str, doc_id: str, page: int):
    try:
        path = pkg.page_image(pid, doc_id, page)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/intake/packages/{pid}/excel")
def packages_excel(pid: str):
    try:
        path = pkg.excel_path(pid)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    return _download(path, f"Пакет_{pid}.xlsx")


# ------------------------------------------------------------------ «добавить в сверку» (ретро)
class RetroBody(BaseModel):
    doc_ids: Optional[list[str]] = None      # пусто — все документы пакета, у которых есть слот ретро-режима


def _own_fields(d: dict, slot: str, cfg: dict) -> tuple[dict, list]:
    """Поля документа для слота. Документ пакета ВЭД, прочитанный разборщиком слота (package_docs),
    переносится только с полями этого разборщика — так же, как файл, загруженный прямо в слот;
    поля общего разбора (подписи на другом языке и т. п.) остаются в «Пакет.xlsx»."""
    fields, items = d.get("fields") or {}, d.get("items") or []
    if ((cfg.get("sources") or {}).get(slot) or {}).get("package") and str(d.get("extractor", "")).startswith("ВЭД-пакет"):
        own = lambda fv: str(fv.get("how", "")).startswith("ВЭД-пакет")  # noqa: E731
        f2 = {f: fv for f, fv in fields.items() if own(fv)}
        i2 = [dict(it, fields={f: fv for f, fv in it["fields"].items() if own(fv)}) for it in items]
        i2 = [it for it in i2 if it["fields"]]
        if f2 or i2:
            return f2, i2
    return fields, items


def _doc_rows(d: dict, slot: str, cfg: dict, pid: str) -> list[dict]:
    fields, items = _own_fields(d, slot, cfg)
    head = {f: fv.get("value") for f, fv in fields.items()}
    rows = [dict(head, **{f: fv.get("value") for f, fv in it["fields"].items()}) for it in items] \
        or ([head] if head else [])
    fmap = ((cfg.get("sources") or {}).get(slot) or {}).get("fields") or {}
    out = []
    for r in rows:
        rec = {fmap.get(k, k): v for k, v in r.items()}
        rec["source_file"] = f"{d['file']} (пакет {pid}, {d['id']})"
        out.append(rec)
    return out


def _enterprise_slots(pid: str, d: dict, cfg: dict) -> list[str]:
    """Файл обмена 1С EnterpriseData (весь файл, не часть) -> слоты, для которых в нём есть данные."""
    if d.get("format") != "xml" or d.get("split") or not d.get("stored"):
        return []
    try:
        from app.services import enterprise_data
        path, _ = pkg.original_path(pid, d["id"])
        with open(path, "rb") as fh:
            data = fh.read()
        if not enterprise_data.is_enterprise_data(data):
            return []
        parts, _ = enterprise_data.parse(data, path)
    except Exception:  # noqa: BLE001 — не EnterpriseData: обычный путь
        return []
    return [s for s in parts if s in (cfg.get("sources") or {})]


def to_retro(pid: str, doc_ids: list | None, user: str) -> dict:
    """Документы пакета -> слоты ретро-режима (как «Сканы → источник»): ДТ, 报关单, SAP, черновики,
    инвойсы, упаковочные листы, транспортные документы, сертификаты, выписки банка, реестр договоров."""
    srv = _srv()
    cfg = srv._cfg()
    docs = pkg.documents(pid)
    chosen = [d for d in docs if (doc_ids is None or d["id"] in doc_ids)]
    by_slot: dict[str, list] = {}
    skipped = []
    for d in chosen:
        slot = RETRO_SLOTS.get(d["type"])
        ed_slots = _enterprise_slots(pid, d, cfg)
        if ed_slots:                          # 1С EnterpriseData: один файл — в несколько слотов
            if d.get("status") in ("не распознан", "ошибка"):
                skipped.append({"doc_id": d["id"], "reason": d.get("status")})
                continue
            path, name = pkg.original_path(pid, d["id"])
            with open(path, "rb") as fh:
                data = fh.read()
            for s_ in ed_slots:
                by_slot.setdefault(s_, []).append(("file", data, f"пакет_{d['id']}_{name}", d["id"]))
            continue
        if slot is None or slot not in (cfg.get("sources") or {}):
            if doc_ids is not None:
                skipped.append({"doc_id": d["id"], "reason": f"тип «{cls_mod.type_label(d['type'])}» в сверку не "
                                                            "переносится"})
            continue
        if d.get("status") in ("не распознан", "ошибка"):
            skipped.append({"doc_id": d["id"], "reason": d.get("status")})
            continue
        whole = not d.get("split") and d.get("format") in WHOLE_FORMATS.get(slot, WHOLE_FORMATS["default"]) \
            and d.get("stored")
        if whole:
            try:
                path, name = pkg.original_path(pid, d["id"])
                with open(path, "rb") as fh:
                    by_slot.setdefault(slot, []).append(("file", fh.read(), f"пакет_{d['id']}_{name}", d["id"]))
                continue
            except pkg.NotFound:
                pass
        rows = _doc_rows(d, slot, cfg, pid)
        if rows:
            by_slot.setdefault(slot, []).append(("rows", rows, f"пакет_{d['id']}.csv", d["id"]))
        else:
            skipped.append({"doc_id": d["id"], "reason": "нет извлечённых полей"})
    # Пакет без выгрузки FineBI: собираем wide table из деклараций пакета,
    # иначе проверки 0–5 не могут запуститься (см. app/services/wide_from_package.py)
    wide_generated = False
    existing = srv._all_sources(cfg)
    if "wide_table" not in by_slot and existing.get("wide_table") is None:
        from app.services import wide_from_package as wfp
        if wfp.has_declarations(chosen):
            rows = wfp.build_rows(chosen)
            if rows:
                by_slot["wide_table"] = [("rows", rows, "пакет_wide_table.csv", None)]
                wide_generated = True

    added = {}
    for slot, entries in by_slot.items():
        files = []
        for kind, payload, name, _ in entries:
            if kind == "file":
                files.append((payload, srv._safe_name(name)))
            else:
                files.append((pd.DataFrame(payload).to_csv(index=False).encode("utf-8-sig"), srv._safe_name(name)))
        multi = bool(cfg["sources"][slot].get("multi"))
        if multi:
            names = {n for _, n in files}
            files = [f for f in srv._slot_files(slot) if f[1] not in names] + files
        else:
            files = files[-1:]
        try:
            src = srv._ingest(slot, files, cfg)
        except Exception as exc:  # noqa: BLE001
            skipped += [{"doc_id": e[3], "reason": f"слот {slot}: {exc}"} for e in entries]
            continue
        srv._store_files(slot, files, append=False)
        added[slot] = {"documents": [e[3] for e in entries if e[3]], "rows": int(len(src.df)),
                       "replaced": not multi}
        if slot == "wide_table" and wide_generated:
            added[slot]["source"] = "собрано из деклараций пакета"
            added[slot]["note"] = ("выгрузки FineBI в пакете нет — таблица собрана из ДТ и 报关单; "
                                   "проверки 0–5 работают на данных деклараций")
    # «SAP vs CD» — ручная сводная таблица. Если её нет, но есть выгрузка SAP (purchase),
    # собираем из номеров деклараций, которые SAP знает: этого достаточно проверке 0 (полнота).
    try:
        if "sap_vs_cd" not in added and srv._all_sources(cfg).get("sap_vs_cd") is None:
            pur = srv._all_sources(cfg).get("purchase")
            if pur is not None and getattr(pur, "df", None) is not None and len(pur.df):
                nums = []
                # ищем колонку с номером декларации: и по каноническому ключу, и по заголовку файла
                cand = [c for c in pur.df.columns
                        if str(c) in ("decl_ru", "decl_num", "decl_cn")
                        or any(k in str(c).lower() for k in ("decl", "деклар", "报关", "报關"))]
                for col in cand:
                    vals = [str(v).strip() for v in pur.df[col].tolist() if str(v).strip()]
                    if vals:
                        nums = vals
                        break
                nums = list(dict.fromkeys(str(n).strip() for n in nums))
                if nums:
                    csv = pd.DataFrame([{"decl_cn": n} for n in nums]).to_csv(index=False).encode("utf-8-sig")
                    src = srv._ingest("sap_vs_cd", [(csv, srv._safe_name("пакет_sap_vs_cd.csv"))], cfg)
                    srv._store_files("sap_vs_cd", [(csv, srv._safe_name("пакет_sap_vs_cd.csv"))], append=False)
                    added["sap_vs_cd"] = {"documents": [], "rows": int(len(src.df)), "replaced": True,
                                          "source": "собрано из выгрузки SAP",
                                          "note": "номеров деклараций из SAP: %d" % len(nums)}
    except Exception as exc:  # noqa: BLE001
        skipped.append({"doc_id": None, "reason": f"sap_vs_cd: {exc}"})

    journal.log("intake_to_retro", user, f"пакет {pid}: " + (", ".join(f"{k}: {len(v['documents'])} док."
                                                                       for k, v in added.items()) or "нечего добавить"),
                meta={"package": pid, "added": added, "skipped": skipped})
    return {"added": added, "skipped": skipped}


@router.post("/intake/packages/{pid}/to-retro")
def packages_to_retro(pid: str, request: Request, body: Optional[RetroBody] = None):
    try:
        return to_retro(pid, (body.doc_ids if body else None), _user(request))
    except Exception as exc:  # noqa: BLE001
        _err(exc)


# ------------------------------------------------------------------ реальное время
class RealtimeBody(BaseModel):
    declarations: Optional[list[str]] = None              # id документов-деклараций; пусто — все сканы ДТ / 报关单
    draft: Union[str, list[str]] = "auto"                 # auto | none | [id черновика]
    system: Union[str, list[str]] = "auto"                # auto | package | retro | none | [id выгрузок]
    references: bool = True
    database: bool = False


@router.post("/realtime/{pid}/check")
def realtime_check(pid: str, request: Request, body: Optional[RealtimeBody] = None):
    from app import realtime
    body = body or RealtimeBody()
    srv = _srv()
    cfg = srv._cfg()
    retro = {k: v for k, v in srv._uploads.items() if k in ("purchase", "wide_table")}
    try:
        res = realtime.run(pid, declarations=body.declarations, draft=body.draft, system=body.system,
                           references=body.references, database=body.database, retro_sources=retro, cfg=cfg,
                           user=_user(request))
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    c = res["counts"]
    journal.log("realtime_check", _user(request), f"пакет {pid}: деклараций {len(res['declarations'])}, "
                f"OK {c['OK']}, REVIEW {c['REVIEW']}, ISSUE {c['ISSUE']}",
                meta={"package": pid, "counts": c, "declarations": [d["doc_id"] for d in res["declarations"]]})
    return res


@router.get("/realtime/rules")
def realtime_rules_get():
    from app.config_loader import load_rules
    return load_rules("realtime.yaml")


@router.put("/realtime/rules")
def realtime_rules_put(rules: dict, request: Request):
    from app.config_loader import save_rules
    try:
        res = save_rules("realtime.yaml", rules)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    journal.log("rules_change", _user(request), f"realtime.yaml: изменений {len(res['diff'])}",
                meta={"file": "realtime.yaml", "diff": res["diff"]})
    return {"saved": True, "diff": res["diff"]}


@router.get("/realtime/{pid}/result")
def realtime_result(pid: str):
    from app import realtime
    try:
        res = realtime.last_result(pid)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    if res is None:
        raise HTTPException(404, "Проверка отгрузки по этому пакету ещё не запускалась (POST /api/realtime/{id}/check)")
    return res


@router.get("/realtime/{pid}/excel")
def realtime_excel(pid: str):
    from app import realtime
    try:
        path = realtime.excel_path(pid)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    if not path:
        raise HTTPException(404, "Проверка отгрузки по этому пакету ещё не запускалась")
    return _download(path, f"Проверка отгрузки {pid}.xlsx")
