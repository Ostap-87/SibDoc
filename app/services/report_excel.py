from __future__ import annotations
# -*- coding: utf-8 -*-
"""Excel-отчёт: сводка, источники, качество данных, лист на каждую проверку,
полные листы деклараций «ДТ РФ» и «报关单 КНР», курсы пересчёта.

Заголовки — по-русски и по-китайски («Контейнер / 集装箱号»), чтобы отчёт читали
обе стороны. Значения из входных файлов, похожие на формулы («=…», «+…», «@…»),
записываются как текст — открытие отчёта не выполняет чужих формул.
"""
import os
import re

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.services import labels
from app.services.engine import RunResult

FILLS = {"OK": PatternFill("solid", fgColor="C6EFCE"),
         "ISSUE": PatternFill("solid", fgColor="FFC7CE"),
         "REVIEW": PatternFill("solid", fgColor="FFEB9C"),
         "SKIPPED": PatternFill("solid", fgColor="D9D9D9"),
         "ERROR": PatternFill("solid", fgColor="FFC7CE"),
         "WARNING": PatternFill("solid", fgColor="FFEB9C"),
         "INFO": PatternFill("solid", fgColor="DDEBF7")}
FONTS = {"OK": Font(color="006100"), "ISSUE": Font(color="9C0006", bold=True),
         "REVIEW": Font(color="9C6500"), "SKIPPED": Font(color="595959"),
         "ERROR": Font(color="9C0006", bold=True), "WARNING": Font(color="9C6500"),
         "INFO": Font(color="1F4E79")}
HEADER_FILL = PatternFill("solid", fgColor="1A56DB")
WRAP_COLS = {"detail", "issue", "impact", "examples", "sap_lines", "declared_elements", "reference_elements",
             "product_name", "decl_elements", "fx_rate", "fx_rate_ru", "checks", "Пояснение"}
_FORMULA = re.compile(r"^[=+@\-]")


def safe_cell(v):
    """Текст, похожий на формулу, -> текст с апострофом (защита от выполнения формул)."""
    if isinstance(v, str) and _FORMULA.match(v):
        try:
            float(v.replace(",", ".").replace(" ", ""))
            return v                      # «-12,5» — просто число в тексте
        except ValueError:
            return "'" + v
    return v


def _safe_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if not pd.api.types.is_numeric_dtype(out[c]):      # object и строковые типы pandas 3
            out[c] = out[c].map(safe_cell).astype(object)
    return out


def _sheet_name(name: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", name)[:31] or "Sheet"
    n, out = 1, base
    while out in used:
        n += 1
        out = f"{base[:28]}-{n}"
    used.add(out)
    return out


def _write(xw, df: pd.DataFrame, sheet: str, used: set, headers: list[str] | None = None,
           startrow: int = 0) -> str:
    name = _sheet_name(sheet, used) if startrow == 0 else sheet
    data = _safe_df(df)
    if headers is not None:
        data.columns = headers
    data.to_excel(xw, sheet_name=name, index=False, startrow=startrow)
    return name


def _decl_frame(src, slot_fields) -> tuple[pd.DataFrame, list[str]]:
    df = src.df.copy()
    df = df.loc[:, [c for c in df.columns if not df[c].isna().all()]]
    return df, [labels.bilingual(c, slot_fields) for c in df.columns]


def write_excel(result: RunResult, cfg: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    used: set = set()
    product = (cfg.get("product") or {}).get("name", "PostControl Suite")
    summary = pd.DataFrame(result.summary_rows())
    if not summary.empty:
        summary["check"] = [labels.check_title(c) for c in summary["check_id"]]
        summary = summary[["check", "total", "OK", "ISSUE", "REVIEW", "SKIPPED"]]
    meta = pd.DataFrame([
        {"Параметр / 参数": "Продукт", "Значение / 数值": product},
        {"Параметр / 参数": "Номер прогона / 运行编号", "Значение / 数值": result.run_id},
        {"Параметр / 参数": "Сформирован / 生成时间", "Значение / 数值": result.created_at},
        {"Параметр / 参数": "Кто запустил / 运行人", "Значение / 数值": result.user},
        {"Параметр / 参数": "Время прогона, с / 用时(秒)", "Значение / 数值": result.duration_sec},
        {"Параметр / 参数": "Всего расхождений (ISSUE) / 不一致合计", "Значение / 数值": result.total_issues()},
        {"Параметр / 参数": "Качество данных: ошибок / 数据错误", "Значение / 数值": result.dq_counts()["ERROR"]},
        {"Параметр / 参数": "Качество данных: предупреждений / 数据警告", "Значение / 数值": result.dq_counts()["WARNING"]},
    ])
    dq_cols = ["severity", "source", "section", "column", "issue", "affected_rows", "examples", "impact"]
    dq = pd.DataFrame([f.as_dict() for f in result.findings]) if result.findings else pd.DataFrame(columns=dq_cols)
    src = pd.DataFrame(result.sources_info)

    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        s_name = _write(xw, meta, "Сводка", used)
        if not summary.empty:
            summary.to_excel(xw, sheet_name=s_name, index=False, startrow=len(meta) + 2,
                             header=[labels.bilingual(c) for c in summary.columns])
        if not src.empty:
            _write(xw, src, "Источники", used, [labels.bilingual(c) for c in src.columns])
        _write(xw, dq, "Качество данных", used, [labels.bilingual(c) for c in dq.columns])
        status_sheets = {}
        for cid, out in result.outputs.items():
            df = out.df.copy()
            if "check" in df.columns:
                df = df.drop(columns="check")
            name = _write(xw, df, labels.SHEETS.get(cid, cid), used, [labels.bilingual(c) for c in df.columns])
            status_sheets[name] = list(df.columns)
        # декларации целиком
        for slot, title in (("ru_dt", "ДТ РФ"), ("cn_dec", "报关单 КНР")):
            s = result.sources.get(slot)
            if s is not None and not s.df.empty:
                df, hdr = _decl_frame(s, ((cfg.get("sources") or {}).get(slot) or {}).get("fields"))
                _write(xw, df, title, used, hdr)
        if result.rates_used:
            _write(xw, pd.DataFrame({"Курс / 汇率": result.rates_used}), "Курсы", used)

        wb = xw.book
        thin = Side(style="thin", color="D0D5DD")
        for ws in wb.worksheets:
            hdr_row = 1
            if ws.title == s_name:
                hdr_row = None
            max_w = {}
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    ln = len(str(v)) if v is not None else 0
                    max_w[cell.column] = max(max_w.get(cell.column, 0), min(ln, 80))
                    cell.alignment = Alignment(vertical="top", wrap_text=ln > 60)
            for ci, w in max_w.items():
                ws.column_dimensions[get_column_letter(ci)].width = max(10, min(60, w + 2))
            header_rows = [1] if hdr_row else [1, len(meta) + 3]
            for r in header_rows:
                if r > ws.max_row:
                    continue
                for cell in ws[r]:
                    if cell.value is not None:
                        cell.font = Font(bold=True, color="FFFFFF")
                        cell.fill = HEADER_FILL
                        cell.alignment = Alignment(vertical="center", wrap_text=True)
                        cell.border = Border(bottom=thin)
            if hdr_row:
                ws.freeze_panes = "A2"
                if ws.max_row > 1 and ws.max_column > 0:
                    ws.auto_filter.ref = ws.dimensions
            # раскраска статусов
            hdr = [str(c.value or "") for c in ws[header_rows[-1]]] if ws.max_row else []
            for key in ("Статус", "Важность"):
                idx = next((i for i, h in enumerate(hdr, 1) if h.startswith(key)), None)
                if idx is None:
                    continue
                for row in ws.iter_rows(min_row=header_rows[-1] + 1, min_col=idx, max_col=idx):
                    cell = row[0]
                    if cell.value in FILLS:
                        cell.fill = FILLS[cell.value]
                        cell.font = FONTS[cell.value]
            # итоговая таблица на «Сводке»: раскрасить счётчики
            if ws.title == s_name:
                for row in ws.iter_rows(min_row=len(meta) + 4, max_row=ws.max_row):
                    for cell, key in zip(row[2:6], ("OK", "ISSUE", "REVIEW", "SKIPPED")):
                        if isinstance(cell.value, (int, float, np.integer)) and cell.value:
                            cell.fill = FILLS[key]
                            cell.font = FONTS[key]
    return out_path
