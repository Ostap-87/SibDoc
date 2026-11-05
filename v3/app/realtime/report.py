from __future__ import annotations
# -*- coding: utf-8 -*-
"""Excel «Проверка отгрузки.xlsx»: сводка, сверка по полям и строкам, источники.

Подписи — по-русски и по-китайски: отчёт читают обе стороны.
"""
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app.intake.excel import SECTION_FONT, autosize, header_row
from app.services.report_excel import FILLS, FONTS, safe_cell

COLUMNS = [("declaration", "Декларация / 报关单"), ("level", "Где / 位置"), ("label", "Поле"), ("label_zh", "字段"),
           ("scan", "Скан / 扫描件"), ("draft", "Черновик / 草稿"), ("system", "Система / 系统数据"),
           ("reference", "Справочник / 参考表"), ("database", "БД / 数据库"), ("status", "Статус / 状态"),
           ("reason", "Причина"), ("reason_zh", "原因"), ("scan_conf", "Уверенность скана / 置信度"),
           ("scan_source", "Где на скане / 来源"), ("matched_by", "Строки сопоставлены по"), ("field", "Код поля")]


def _status_cell(cell) -> None:
    if cell.value in FILLS:
        cell.fill = FILLS[cell.value]
        cell.font = FONTS.get(cell.value, Font())


def write_excel(result: dict, path: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    ws.cell(row=1, column=1, value=f"Проверка отгрузки / 发货核查 · пакет {result['package_id']} · {result['created']}"
            ).font = SECTION_FONT
    c = result["counts"]
    ws.cell(row=2, column=1, value=f"Всего строк сверки: {c['total']} · OK {c['OK']} · REVIEW {c['REVIEW']} · "
                                   f"ISSUE {c['ISSUE']}")
    header_row(ws, 4, ["Документ", "Декларация / 报关单", "Тип", "Файл", "Черновик / 草稿", "Система / 系统数据",
                       "БД / 数据库", "OK", "REVIEW", "ISSUE", "Итог / 结论"])
    for i, d in enumerate(result["declarations"], start=5):
        cn = d["counts"]
        verdict = "ISSUE" if cn.get("ISSUE") else ("REVIEW" if cn.get("REVIEW") else "OK")
        vals = [d["doc_id"], d["number"], d["type_label"], d["file"],
                (d["draft"].get("file") or "—") + (f" ({d['draft'].get('reason')})" if d["draft"].get("reason") else ""),
                f"{d['system']['source']}: {d['system']['lines']} стр. — {d['system']['reason']}",
                f"{d['database']['source']}: {d['database']['reason']}", cn.get("OK", 0), cn.get("REVIEW", 0),
                cn.get("ISSUE", 0), verdict]
        for col, v in enumerate(vals, start=1):
            ws.cell(row=i, column=col, value=safe_cell(v))
        _status_cell(ws.cell(row=i, column=11))
    autosize(ws, 60)

    ws = wb.create_sheet("Сверка")
    header_row(ws, 1, [t for _, t in COLUMNS])
    r = 2
    for d in result["declarations"]:
        for row in d["rows"]:
            for col, (k, _) in enumerate(COLUMNS, start=1):
                v = row.get(k)
                if k == "scan_conf" and v is not None:
                    cell = ws.cell(row=r, column=col, value=float(v))
                    cell.number_format = "0%"
                    continue
                cell = ws.cell(row=r, column=col, value=safe_cell(v if v is not None else ""))
                if k in ("reason", "reason_zh"):
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
            _status_cell(ws.cell(row=r, column=10))
            r += 1
    ws.freeze_panes = "E2"
    ws.auto_filter.ref = f"A1:P{max(2, r - 1)}"
    autosize(ws, 55)

    ws = wb.create_sheet("Источники")
    header_row(ws, 1, ["Декларация", "Что", "Источник", "Пояснение"])
    r = 2
    for d in result["declarations"]:
        entries = [("скан / 扫描件", d["file"], d["type_label"]),
                   ("черновик / 草稿", d["draft"].get("file") or "—", d["draft"].get("reason", "")),
                   ("система / 系统数据", "; ".join(d["system"]["docs"]) or "—",
                    f"{d['system']['reason']} {d['system'].get('note') or ''}".strip()),
                   ("БД / 数据库", "; ".join(d["database"]["docs"]) or "—", d["database"]["reason"])]
        entries += [("справочник / 参考表", x, "") for x in d.get("references") or []]
        for what, src, why in entries:
            for col, v in enumerate([d["number"] or d["doc_id"], what, src, why], start=1):
                ws.cell(row=r, column=col, value=safe_cell(v))
            r += 1
    autosize(ws, 80)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, path)
    return path
