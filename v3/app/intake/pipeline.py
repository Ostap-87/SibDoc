from __future__ import annotations
# -*- coding: utf-8 -*-
"""Пакетная загрузка: часть файла -> документы (деление) -> тип -> поля.

Деление многодокументного файла (PDF, многостраничный TIFF, книга Excel):
каждая страница / лист классифицируется отдельно; новый документ начинается
там, где в шапке страницы есть заголовок другого типа («PACKING LIST» после
инвойса) или того же типа с другим номером (два инвойса подряд). Страницы
без заголовка (продолжение таблицы, оборот) присоединяются к предыдущему
документу. Тип документа целиком определяется по всем его страницам.
"""
from app.intake import classify as cls_mod
from app.intake import extract as ex
from app.intake.fieldmap import find_header
from app.intake.formats import Part, Unit

STATUS_OK = "распознан"
STATUS_REVIEW = "требует проверки"
STATUS_UNKNOWN = "не распознан"
STATUS_ERROR = "ошибка"
STATUSES = (STATUS_OK, STATUS_REVIEW, STATUS_UNKNOWN, STATUS_ERROR)


def _top(unit: Unit, n: int) -> str:
    # заголовок — короткая строка в начале страницы; длинная фраза («во вложении инвойс и упаковочный лист…») — нет
    lines = [ln.text for ln in unit.lines if ln.text.strip()][:n]
    lines = [t for t in lines if len(t.strip()) <= 90]
    return "\n".join(([unit.sheet] if unit.sheet else []) + lines)


def _headers(units: list) -> set:
    out = set()
    for u in units:
        for grid in u.tables or []:
            _, mapping = find_header(grid)
            out |= set(mapping.values())
    return out


def features(units: list, part: Part) -> cls_mod.Features:
    st, _ = cls_mod.config()
    n = int(st.get("title_lines", 6))
    return cls_mod.Features(text="\n".join(u.text for u in units), top="\n".join(_top(u, n) for u in units[:1]),
                            filename=part.name, fmt=part.fmt, header_fields=_headers(units),
                            xml_root=next((u.xml_root for u in units if u.xml_root), ""))


def segment(part: Part) -> list[dict]:
    """-> [{"units": [...], "reason": "почему здесь начат документ"}]."""
    units = [u for u in part.units]
    if len(units) <= 1:
        return [{"units": units, "reason": ""}]
    st, _ = cls_mod.config()
    groups: list[dict] = []
    for u in units:
        c = cls_mod.classify(features([u], part))
        t = c.title_type
        if not groups:
            groups.append({"units": [u], "type": t or (c.type if c.score >= st["min_score"] else ""),
                           "id": c.doc_id, "reason": ""})
            continue
        g = groups[-1]
        new_type = t and t != g["type"]
        new_id = t and t == g["type"] and c.doc_id and g["id"] and c.doc_id != g["id"]
        if new_type or new_id:
            why = (f"{u.label}: заголовок «{cls_mod.type_label(t)}»" if new_type else
                   f"{u.label}: другой номер документа ({g['id']} → {c.doc_id})")
            groups.append({"units": [u], "type": t, "id": c.doc_id, "reason": why})
        else:
            g["units"].append(u)
            g["id"] = g["id"] or c.doc_id
            g["type"] = g["type"] or t
    return groups


def page_range(units: list) -> list | None:
    pages = [u.index for u in units if u.kind == "page"]
    return [min(pages), max(pages)] if pages else None


def build_document(doc_id: str, part: Part, units: list, split: dict | None = None,
                   forced_type: str | None = None) -> dict:
    """Документ: тип (или заданный вручную), поля, товары, статус."""
    doc = {"id": doc_id, "part": part.no, "file": part.path, "name": part.name, "container": part.container,
           "format": part.fmt, "converted_from": part.converted_from, "stored": part.stored,
           "pages": page_range(units), "sheets": [u.sheet for u in units if u.sheet],
           "units": [u.to_dict() for u in units], "split": split or {}, "part_status": part.status,
           "part_message": part.message}
    if part.status != "ok" or not any(u.lines or u.tables for u in units):
        c = cls_mod.Classification("other", 0.0, 0.0, {}, [part.message or "текст не найден"])
        if part.status == "ok":
            c = cls_mod.classify(features(units, part))
    else:
        c = cls_mod.classify(features(units, part))
    doc["type_auto"] = c.type
    doc["type"] = forced_type or c.type
    doc["type_source"] = "manual" if forced_type else "auto"
    doc["confidence"] = 1.0 if forced_type else c.confidence
    doc["classification"] = c.as_dict()
    doc["doc_number"] = c.doc_id
    run_extraction(doc, part.data)
    return doc


def run_extraction(doc: dict, data: bytes = b"") -> dict:
    units = [Unit.from_dict(u) for u in doc.get("units") or []]
    if doc.get("part_status", "ok") != "ok" or not any(u.lines or u.tables for u in units):
        res = {"fields": {}, "items": [], "kv": [], "warnings": [], "extractor": "нет"}
    else:
        ctx = ex.DocContext(doc_id=doc["id"], type=doc["type"], units=units, file=doc["file"],
                            fmt=doc["format"], data=data)
        res = ex.extract(ctx)
    doc.update(fields=res["fields"], items=res["items"], kv=res.get("kv") or [], warnings=res.get("warnings") or [],
               extractor=res.get("extractor", ""))
    if doc["type"] != doc.get("type_auto") or doc.get("type_source") == "manual":
        num = cls_mod.find_doc_id(cls_mod.config()[1].get(doc["type"]), "\n".join(u.text for u in units))
        doc["doc_number"] = num or doc.get("doc_number", "")
    doc["status"], doc["status_reason"] = status_of(doc, units)
    return doc


def low_conf_fields(doc: dict, threshold: float | None = None) -> list[tuple[str, str, dict]]:
    """[(где: «шапка» | «строка N», поле, значение)] с уверенностью ниже порога или не прошедшие проверку."""
    thr = float(ex.config().get("field_review_below", 0.75)) if threshold is None else threshold
    out = []
    for f, fv in (doc.get("fields") or {}).items():
        if fv.get("conf", 1) < thr or not fv.get("valid", True):
            out.append(("шапка", f, fv))
    for it in doc.get("items") or []:
        for f, fv in (it.get("fields") or {}).items():
            if fv.get("conf", 1) < thr or not fv.get("valid", True):
                out.append((f"строка {it.get('no')}", f, fv))
    return out


def status_of(doc: dict, units: list) -> tuple[str, str]:
    if doc.get("part_status") == "unsupported":
        return STATUS_UNKNOWN, doc.get("part_message") or "формат не поддерживается"
    if doc.get("part_status") == "error":
        return STATUS_ERROR, doc.get("part_message") or "ошибка чтения"
    if not any(u.lines or u.tables for u in units):
        warns = [w for u in units for w in u.warnings]
        return STATUS_UNKNOWN, doc.get("part_message") or "; ".join(warns) or "текст не найден"
    st, _ = cls_mod.config()
    why = []
    c = doc.get("classification") or {}
    if doc.get("type_source") != "manual":
        if doc["type"] == "other" and float(c.get("score", 0)) < st["min_score"]:
            why.append("тип не определён — укажите вручную")
        elif float(doc.get("confidence", 0)) < float(st["review_below"]):
            why.append(f"уверенность в типе {float(doc.get('confidence', 0)):.0%}")
    low = low_conf_fields(doc)
    if low:
        why.append(f"полей с низкой уверенностью: {len(low)}")
    why += [w for w in doc.get("warnings") or [] if w.startswith("не найдено поле")]
    return (STATUS_REVIEW, "; ".join(why)) if why else (STATUS_OK, "")


def documents_from_part(part: Part, next_id) -> list[dict]:
    """Часть файла -> документы (с делением многодокументных файлов)."""
    groups = segment(part) if part.status == "ok" else [{"units": part.units, "reason": ""}]
    docs = []
    for k, g in enumerate(groups, start=1):
        split = {"index": k, "of": len(groups), "reason": g.get("reason", "")} if len(groups) > 1 else {}
        docs.append(build_document(next_id(), part, g["units"], split))
    return docs
