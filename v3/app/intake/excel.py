from __future__ import annotations
# -*- coding: utf-8 -*-
"""Excel «Пакет.xlsx»: все документы пакета в одной книге.

Листы:
  «Документы»        — файл, страницы / листы, тип, уверенность, статус, причины;
  лист на каждый тип — «Поля» (строка = документ, колонка = поле словаря; в
                       примечании ячейки — откуда значение) и «Товарные строки»;
  «Требует проверки» — поля с низкой уверенностью / не прошедшие проверку формата;
  «Источники полей»  — каждое значение: файл, страница / лист, строка, способ, уверенность.
"""
import os

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app import fields as fdict
from app.intake import classify as cls_mod
from app.intake import pipeline
from app.services.report_excel import FILLS, HEADER_FILL, _sheet_name, safe_cell

STATUS_FILL = {pipeline.STATUS_OK: FILLS["OK"], pipeline.STATUS_REVIEW: FILLS["REVIEW"],
               pipeline.STATUS_UNKNOWN: FILLS["SKIPPED"], pipeline.STATUS_ERROR: FILLS["ISSUE"]}
LOW_FILL = PatternFill("solid", fgColor="FFEB9C")
SECTION_FONT = Font(bold=True, size=12, color="1A56DB")
METHOD_RU = {"xml": "XML", "table": "таблица", "text": "текстовый слой", "ocr": "OCR", "pattern": "шаблон"}


def fmt_value(v) -> object:
    if isinstance(v, float):
        return int(v) if v.is_integer() else round(v, 6)
    return safe_cell(v)


def where(doc: dict) -> str:
    if doc.get("pages"):
        a, b = doc["pages"]
        return f"стр. {a}" if a == b else f"стр. {a}–{b}"
    if doc.get("sheets"):
        return ", ".join(f"лист «{s}»" for s in doc["sheets"])
    return ""


def source_text(fv: dict) -> str:
    s = fv.get("source") or {}
    parts = [s.get("file", "")]
    if s.get("page"):
        parts.append(f"стр. {s['page']}")
    if s.get("sheet"):
        parts.append(f"лист «{s['sheet']}»")
    if s.get("row"):
        parts.append(f"строка {s['row']}")
    if s.get("col"):
        parts.append(f"колонка {s['col']}")
    if s.get("line"):
        parts.append(f"строка текста {s['line']}")
    parts.append(METHOD_RU.get(s.get("method", ""), s.get("method", "")))
    if s.get("ocr_conf") is not None:
        parts.append(f"OCR {float(s['ocr_conf']):.0%}")
    return ", ".join(p for p in parts if p)


def header_row(ws, row: int, values: list) -> None:
    for c, v in enumerate(values, start=1):
        cell = ws.cell(row=row, column=c, value=v)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def autosize(ws, max_width: int = 60) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                widths[cell.column] = max(widths.get(cell.column, 0), min(max_width, len(str(cell.value)) + 2))
    for c, w in widths.items():
        ws.column_dimensions[get_column_letter(c)].width = max(10, w)


def _bilingual(f: str) -> str:
    ru, zh = fdict.label(f, "ru"), fdict.label(f, "zh")
    return ru if ru == zh else f"{ru} / {zh}"


def _field_order(docs: list, key: str) -> list:
    order = list(fdict.dictionary())
    seen: list[str] = []
    for d in docs:
        src = d.get("fields") if key == "fields" else {f: 1 for it in d.get("items") or [] for f in it["fields"]}
        for f in src or {}:
            if f not in seen:
                seen.append(f)
    return sorted(seen, key=lambda f: order.index(f) if f in order else 999)


def _put_value(ws, row: int, col: int, fv: dict, thr: float) -> None:
    cell = ws.cell(row=row, column=col, value=fmt_value(fv.get("value")))
    note = f"{source_text(fv)}; уверенность {fv.get('conf', 0):.0%}"
    if fv.get("raw") not in (None, "") and str(fv.get("raw")) != str(fv.get("value")):
        note += f"; в документе: «{str(fv.get('raw'))[:120]}»"
    if fv.get("note"):
        note += f"; {fv['note']}"
    cell.comment = Comment(note, "SibDoc", width=320, height=90)
    if fv.get("conf", 1) < thr or not fv.get("valid", True):
        cell.fill = LOW_FILL


def write_package_excel(meta: dict, docs: list, path: str) -> str:
    from app.intake import extract as ex
    thr = float(ex.config().get("field_review_below", 0.75))
    wb = Workbook()
    ws = wb.active
    ws.title = "Документы"
    used = {"Документы"}
    ws.cell(row=1, column=1, value=f"Пакет {meta.get('id')} · режим «{meta.get('mode_label')}» · "
                                   f"{meta.get('created')} · документов: {len(docs)}").font = SECTION_FONT
    cols = ["№", "Документ", "Файл", "Откуда (архив / письмо)", "Страницы / листы", "Формат", "Тип", "Код типа",
            "Уверенность", "Как определён", "Статус", "Причина статуса", "Номер документа", "Полей", "Строк товаров",
            "Признаки типа"]
    header_row(ws, 3, cols)
    for i, d in enumerate(docs, start=1):
        r = 3 + i
        vals = [i, d["id"], d["file"], d.get("container") or "", where(d), d.get("format"),
                cls_mod.type_label(d["type"]), d["type"], round(float(d.get("confidence", 0)), 2),
                "вручную" if d.get("type_source") == "manual" else "автоматически", d.get("status"),
                d.get("status_reason") or "", d.get("doc_number") or "", len(d.get("fields") or {}),
                len(d.get("items") or []), "; ".join((d.get("classification") or {}).get("reasons") or [])[:500]]
        for c, v in enumerate(vals, start=1):
            ws.cell(row=r, column=c, value=safe_cell(v))
        ws.cell(row=r, column=9).number_format = "0%"
        st = ws.cell(row=r, column=11)
        if d.get("status") in STATUS_FILL:
            st.fill = STATUS_FILL[d["status"]]
    ws.freeze_panes = "C4"
    autosize(ws)

    # ---- лист на тип документа
    by_type: dict[str, list] = {}
    for d in docs:
        by_type.setdefault(d["type"], []).append(d)
    type_order = [t["type"] for t in cls_mod.types_public()]
    for t in sorted(by_type, key=lambda x: type_order.index(x) if x in type_order else 99):
        group = by_type[t]
        ws = wb.create_sheet(_sheet_name(cls_mod.type_label(t), used))
        ws.cell(row=1, column=1, value=f"{cls_mod.type_label(t)} / {cls_mod.type_label(t, 'zh')} — поля документов"
                ).font = SECTION_FONT
        fcols = _field_order(group, "fields")
        header_row(ws, 2, ["Документ", "Файл", "Стр. / лист", "Номер документа"] + [_bilingual(f) for f in fcols])
        r = 3
        for d in group:
            for c, v in enumerate([d["id"], d["file"], where(d), d.get("doc_number") or ""], start=1):
                ws.cell(row=r, column=c, value=safe_cell(v))
            for k, f in enumerate(fcols, start=5):
                fv = (d.get("fields") or {}).get(f)
                if fv:
                    _put_value(ws, r, k, fv, thr)
            r += 1
        icols = _field_order(group, "items")
        if icols:
            r += 1
            ws.cell(row=r, column=1, value="Товарные строки / 商品明细").font = SECTION_FONT
            r += 1
            header_row(ws, r, ["Документ", "Файл", "№ строки"] + [_bilingual(f) for f in icols])
            r += 1
            for d in group:
                for it in d.get("items") or []:
                    for c, v in enumerate([d["id"], d["file"], it.get("no")], start=1):
                        ws.cell(row=r, column=c, value=safe_cell(v))
                    for k, f in enumerate(icols, start=4):
                        fv = it["fields"].get(f)
                        if fv:
                            _put_value(ws, r, k, fv, thr)
                    r += 1
        if not fcols and not icols:
            ws.cell(row=3, column=1, value="Поля не извлечены: для этого типа пока только исходный текст "
                                           "(см. карточку документа в программе).")
        ws.freeze_panes = "B3"
        autosize(ws, 40)

    # ---- требует проверки
    ws = wb.create_sheet(_sheet_name("Требует проверки", used))
    header_row(ws, 1, ["Документ", "Файл", "Тип", "Где", "Поле", "Поле (RU / ZH)", "Значение", "В документе",
                       "Уверенность", "Источник", "Причина"])
    r = 2
    for d in docs:
        if d.get("status") in (pipeline.STATUS_UNKNOWN, pipeline.STATUS_ERROR) or \
                (d.get("status") == pipeline.STATUS_REVIEW and not pipeline.low_conf_fields(d)):
            vals = [d["id"], d["file"], cls_mod.type_label(d["type"]), "документ", "", "", "", "",
                    round(float(d.get("confidence", 0)), 2), where(d), d.get("status_reason") or d.get("status")]
            for c, v in enumerate(vals, start=1):
                ws.cell(row=r, column=c, value=safe_cell(v))
            r += 1
        for place, f, fv in pipeline.low_conf_fields(d, thr):
            reason = []
            if fv.get("conf", 1) < thr:
                reason.append(f"уверенность {fv.get('conf', 0):.0%} < {thr:.0%}")
            if not fv.get("valid", True):
                reason.append(fv.get("note") or "значение не прошло проверку формата")
            vals = [d["id"], d["file"], cls_mod.type_label(d["type"]), place, f, _bilingual(f),
                    fmt_value(fv.get("value")), str(fv.get("raw") or "")[:200], round(float(fv.get("conf", 0)), 2),
                    source_text(fv), "; ".join(reason)]
            for c, v in enumerate(vals, start=1):
                ws.cell(row=r, column=c, value=safe_cell(v) if not isinstance(v, float) else v)
            ws.cell(row=r, column=9).number_format = "0%"
            r += 1
    if r == 2:
        ws.cell(row=2, column=1, value="Все поля извлечены с достаточной уверенностью.")
    ws.freeze_panes = "A2"
    autosize(ws, 50)

    # ---- источники полей
    ws = wb.create_sheet(_sheet_name("Источники полей", used))
    header_row(ws, 1, ["Документ", "Где", "Поле", "Значение", "Уверенность", "Способ", "Файл", "Стр.", "Лист",
                       "Строка", "Колонка", "Как найдено"])
    r = 2
    for d in docs:
        rows = [("шапка", f, fv) for f, fv in (d.get("fields") or {}).items()]
        rows += [(f"строка {it.get('no')}", f, fv) for it in d.get("items") or [] for f, fv in it["fields"].items()]
        for place, f, fv in rows:
            s = fv.get("source") or {}
            vals = [d["id"], place, f, fmt_value(fv.get("value")), round(float(fv.get("conf", 0)), 2),
                    METHOD_RU.get(s.get("method", ""), s.get("method", "")), s.get("file", ""), s.get("page"),
                    s.get("sheet"), s.get("row") or s.get("line"), s.get("col"), fv.get("how") or ""]
            for c, v in enumerate(vals, start=1):
                ws.cell(row=r, column=c, value=safe_cell(v) if not isinstance(v, float) else v)
            ws.cell(row=r, column=5).number_format = "0%"
            r += 1
    ws.freeze_panes = "A2"
    autosize(ws, 45)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, path)
    return path
