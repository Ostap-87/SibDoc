from __future__ import annotations
# -*- coding: utf-8 -*-
"""PDF-отчёт: сводка, DQ-находки (по секциям/источникам), отклонения по проверкам.

Реализовано на reportlab; кириллица — через встроенный шрифт DejaVu
(ищется в системных путях, в Docker-образе ставится fonts-dejavu).
"""
import os
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from app.services.engine import RunResult

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
_FONT_NAME = "Helvetica"


def _register_font():
    global _FONT_NAME
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                if path.endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont("AppFont", path, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont("AppFont", path))
                _FONT_NAME = "AppFont"
                return
            except Exception:
                continue


_register_font()

STATUS_COLORS = {
    "OK": colors.HexColor("#C6EFCE"), "ISSUE": colors.HexColor("#FFC7CE"),
    "REVIEW": colors.HexColor("#FFEB9C"), "SKIPPED": colors.HexColor("#D9D9D9"),
    "ERROR": colors.HexColor("#FFC7CE"), "WARNING": colors.HexColor("#FFEB9C"),
    "INFO": colors.HexColor("#DDEBF7"),
}


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Title"], fontName=_FONT_NAME, fontSize=18, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=_FONT_NAME, fontSize=13,
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName=_FONT_NAME, fontSize=9),
        "cell": ParagraphStyle("cell", parent=ss["BodyText"], fontName=_FONT_NAME, fontSize=7.5,
                               leading=9),
    }


def _df_table(df: pd.DataFrame, styles, max_rows: int = 40) -> Table:
    if df.empty:
        return Table([[Paragraph("нет данных", styles["cell"])]])
    df = df.head(max_rows)
    cell = styles["cell"]
    data = [[Paragraph(f"<b>{c}</b>", cell) for c in df.columns]]
    for _, row in df.iterrows():
        data.append([Paragraph("" if pd.isna(v) else str(v)[:120], cell) for v in row])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A56DB")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B0B7C3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    # подсветка статусов/severity
    for key in ("status", "severity"):
        if key in list(df.columns):
            ci = list(df.columns).index(key)
            for ri, (_, row) in enumerate(df.iterrows(), start=1):
                val = row[key]
                if val in STATUS_COLORS:
                    t.setStyle(TableStyle([("BACKGROUND", (ci, ri), (ci, ri), STATUS_COLORS[val])]))
    return t


def write_pdf(result: RunResult, cfg: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    st = _styles()
    doc = SimpleDocTemplate(out_path, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    el = []

    product = (cfg.get("product") or {}).get("name", "PostControl Suite")
    el.append(Paragraph(f"{product} — отчёт о проверке", st["h1"]))
    el.append(Paragraph(
        f"Сформирован: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
        f"время прогона: {result.duration_sec} c · "
        f"всего ISSUE: <b>{result.total_issues()}</b> · "
        f"DQ: ERROR={result.dq_counts()['ERROR']}, WARNING={result.dq_counts()['WARNING']}, "
        f"INFO={result.dq_counts()['INFO']}", st["body"]))
    el.append(Spacer(1, 6))

    el.append(Paragraph("Сводка по проверкам", st["h2"]))
    el.append(_df_table(pd.DataFrame(result.summary_rows()), st))

    el.append(Paragraph("Источники данных", st["h2"]))
    el.append(_df_table(pd.DataFrame(result.sources_info), st))

    el.append(Paragraph("Data-quality: где не хватает данных и что некорректно", st["h2"]))
    dq_df = pd.DataFrame([f.as_dict() for f in result.findings])
    el.append(_df_table(dq_df, st, max_rows=60))

    for cid, out in result.outputs.items():
        bad = out.df[out.df["status"].isin(["ISSUE", "REVIEW"])] if "status" in out.df.columns else out.df
        if bad.empty:
            continue
        el.append(Paragraph(f"{out.title} — отклонения и пункты на проверку", st["h2"]))
        el.append(_df_table(bad, st, max_rows=40))

    doc.build(el)
    return out_path
