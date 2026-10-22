from __future__ import annotations
# -*- coding: utf-8 -*-
"""PDF-отчёт: сводка, источники, качество данных, отклонения по проверкам,
декларации «ДТ РФ» и «报关单 КНР» целиком (шапка + товары), курсы пересчёта.

Шрифты: латиница и кириллица — DejaVu Sans; китайские иероглифы — шрифт CJK
(WenQuanYi, Microsoft YaHei, SimSun, Arial Unicode — что найдётся в системе;
в Docker-образе ставится fonts-wqy-zenhei). Текст делится на участки, и
иероглифы печатаются шрифтом CJK, поэтому русский и китайский в одной ячейке
отображаются правильно. Если CJK-шрифта нет, используется встроенный
STSong-Light (без внедрения — нужен просмотрщик с азиатскими шрифтами).
"""
import os
import re
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from app.services import labels
from app.services.engine import RunResult

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_LATIN_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu-sans-fonts/DejaVuSans.ttf", None),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", None),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
# только шрифты с контурами TrueType (glyf): CFF-шрифты (Noto CJK .otc/.ttc) reportlab не встраивает
_CJK_CANDIDATES = [
    os.path.join(PROJECT_ROOT, "assets", "fonts", "cjk.ttf"),
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
FONT, FONT_B, CJK = "Helvetica", "Helvetica-Bold", "Helvetica"
CJK_EMBEDDED = False


def _register_fonts():
    global FONT, FONT_B, CJK, CJK_EMBEDDED
    for reg, bold in _LATIN_CANDIDATES:
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont("AppFont", reg))
                FONT = FONT_B = "AppFont"
                if bold and os.path.exists(bold):
                    pdfmetrics.registerFont(TTFont("AppFontBold", bold))
                    FONT_B = "AppFontBold"
                break
            except Exception:  # noqa: BLE001
                continue
    for path in _CJK_CANDIDATES:
        if os.path.exists(path):
            try:
                kw = {"subfontIndex": 0} if path.lower().endswith(".ttc") else {}
                pdfmetrics.registerFont(TTFont("AppCJK", path, **kw))
                CJK, CJK_EMBEDDED = "AppCJK", True
                return
            except Exception:  # noqa: BLE001
                continue
    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        CJK = "STSong-Light"
    except Exception:  # noqa: BLE001
        CJK = FONT


_register_fonts()

_CJK_RX = re.compile(r"([\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\uf900-\ufaff]+)")

STATUS_COLORS = {
    "OK": colors.HexColor("#C6EFCE"), "ISSUE": colors.HexColor("#FFC7CE"),
    "REVIEW": colors.HexColor("#FFEB9C"), "SKIPPED": colors.HexColor("#D9D9D9"),
    "ERROR": colors.HexColor("#FFC7CE"), "WARNING": colors.HexColor("#FFEB9C"),
    "INFO": colors.HexColor("#DDEBF7"),
}
BLUE = colors.HexColor("#1A56DB")

# какие колонки показывать в PDF по каждой проверке (полные данные — в Excel)
PDF_COLUMNS = {
    "completeness": ["decl_num", "in_wide", "in_manual", "status", "detail"],
    "price": ["container", "decl_num", "contract", "cd_price_per_t", "cd_currency", "sap_price", "sap_currency",
              "status", "detail"],
    "hs_code": ["decl_num", "container", "material", "cd_hs_code", "expected_hs", "status", "detail"],
    "incoterms": ["decl_num", "container", "storage_location", "cd_incoterms", "sap_incoterms", "status", "detail"],
    "decl_elements": ["decl_num", "declared_model", "reference_model", "status", "detail"],
    "contracts": ["container", "decl_num", "cd_contract", "sap_contract", "status", "detail"],
    "cross_border": ["level", "decl_ru", "decl_cn", "containers", "checks", "status", "detail"],
}
RU_HEADER = ["decl_ru", "decl_date", "procedure", "consignor", "consignee", "contract", "currency", "total_value",
             "incoterms", "delivery_place", "customs_office", "source_file"]
RU_GOODS = ["item_no", "hs_code", "product_name", "container", "net_weight", "gross_weight", "total_value",
            "origin_country"]
CN_HEADER = ["decl_cn", "draft_num", "decl_date", "ie_date", "consignee", "consignor", "contract", "incoterms",
             "port", "customs_office", "bill_of_lading", "net_weight_total", "gross_weight_total", "source_file"]
CN_GOODS = ["item_no", "hs_code", "product_name", "decl_elements", "container", "qty", "unit", "unit_price",
            "total_value", "currency", "net_weight", "origin_country"]


def rich(v, limit: int = 400) -> str:
    """Текст для Paragraph: экранирование + иероглифы шрифтом CJK."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, float):
        s = f"{v:,.3f}".rstrip("0").rstrip(".").replace(",", " ")
    else:
        s = str(v)
    s = s[:limit] + ("…" if len(s) > limit else "")
    parts = _CJK_RX.split(s)
    out = []
    for i, p in enumerate(parts):
        if not p:
            continue
        e = escape(p).replace("\n", "<br/>")
        out.append(f'<font name="{CJK}">{e}</font>' if i % 2 == 1 and CJK != FONT else e)
    return "".join(out)


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Title"], fontName=FONT_B, fontSize=17, spaceAfter=4, leading=21),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=FONT_B, fontSize=12.5, spaceBefore=10, keepWithNext=1,
                             spaceAfter=4, textColor=BLUE),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontName=FONT_B, fontSize=10.5, spaceBefore=6,
                             spaceAfter=3),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName=FONT, fontSize=9, leading=12),
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontName=FONT, fontSize=7.5, leading=9.5,
                                textColor=colors.HexColor("#475467")),
        "cell": ParagraphStyle("cell", parent=ss["BodyText"], fontName=FONT, fontSize=7.2, leading=8.8),
        "hcell": ParagraphStyle("hcell", parent=ss["BodyText"], fontName=FONT_B, fontSize=7.2, leading=8.8,
                                textColor=colors.white),
    }


def _table(df: pd.DataFrame, st, headers: list[str] | None = None, max_rows: int = 200,
           widths: list | None = None) -> Table:
    if df is None or df.empty:
        return Table([[Paragraph("нет данных / 无数据", st["cell"])]])
    total = len(df)
    df = df.head(max_rows)
    hdr = headers or list(map(str, df.columns))
    data = [[Paragraph(rich(h), st["hcell"]) for h in hdr]]
    for _, row in df.iterrows():
        data.append([Paragraph(rich(v), st["cell"]) for v in row])
    t = Table(data, repeatRows=1, colWidths=widths)
    style = [("BACKGROUND", (0, 0), (-1, 0), BLUE),
             ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B0B7C3")),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]),
             ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for key in ("status", "severity"):
        if key in df.columns:
            ci = list(df.columns).index(key)
            for ri, val in enumerate(df[key].tolist(), start=1):
                if val in STATUS_COLORS:
                    style.append(("BACKGROUND", (ci, ri), (ci, ri), STATUS_COLORS[val]))
    t.setStyle(TableStyle(style))
    if total > max_rows:
        return KeepTogether([t, Paragraph(f"… показано {max_rows} из {total} строк, полностью — в Excel", st["small"])])
    return t


def _widths(cols: list[str], avail: float) -> list[float]:
    weight = {"detail": 3.2, "checks": 2.2, "product_name": 2.0, "decl_elements": 2.6, "sap_lines": 2.4,
              "issue": 3.0, "impact": 2.0, "examples": 2.0, "consignor": 1.8, "consignee": 1.8, "containers": 1.6,
              "container": 1.3, "level": 1.4, "source_file": 1.6}
    ws = [weight.get(c, 1.0) for c in cols]
    k = avail / sum(ws)
    return [w * k for w in ws]


def _kv_table(pairs: list[tuple[str, object]], st, avail) -> Table:
    data = [[Paragraph(rich(k), st["cell"]), Paragraph(rich(v), st["cell"])] for k, v in pairs if v not in (None, "")]
    if not data:
        return Spacer(1, 1)
    t = Table(data, colWidths=[avail * 0.28, avail * 0.72])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B0B7C3")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF2FF")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _first(series):
    for v in series:
        if v is not None and not (isinstance(v, float) and np.isnan(v)) and str(v).strip() not in ("", "nan"):
            return v
    return None


def _declarations(el: list, src, slot: str, st, avail, max_decl: int = 60):
    df = src.df
    key = "decl_ru" if slot == "ru_dt" else "decl_cn"
    hdr_cols, goods_cols = (RU_HEADER, RU_GOODS) if slot == "ru_dt" else (CN_HEADER, CN_GOODS)
    if key not in df.columns:
        el.append(_table(df.head(50), st, [labels.bilingual(c) for c in df.columns][:12]))
        return
    for n, (num, grp) in enumerate(df.groupby(key, sort=False)):
        if n >= max_decl:
            el.append(Paragraph(f"… ещё {df[key].nunique() - max_decl} деклараций — полностью в Excel", st["small"]))
            break
        el.append(Paragraph(rich(f"{'ДТ' if slot == 'ru_dt' else '报关单'} {num}"), st["h3"]))
        pairs = [(labels.bilingual(c), _first(grp[c])) for c in hdr_cols if c in grp.columns]
        extra = [c for c in grp.columns if c not in hdr_cols + goods_cols + [key] and grp[c].nunique(dropna=True) <= 1]
        pairs += [(labels.bilingual(c), _first(grp[c])) for c in extra]
        el.append(_kv_table(pairs, st, avail))
        gcols = [c for c in goods_cols if c in grp.columns]
        if gcols:
            el.append(Spacer(1, 3))
            el.append(_table(grp[gcols], st, [labels.bilingual(c) for c in gcols],
                             widths=_widths(gcols, avail)))


def write_pdf(result: RunResult, cfg: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    st = _styles()
    page = landscape(A4)
    margin = 12 * mm
    avail = page[0] - 2 * margin
    max_rows = int((cfg.get("output") or {}).get("pdf_max_rows_per_check", 200))
    product = (cfg.get("product") or {}).get("name", "PostControl Suite")

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(margin, 7 * mm, f"{product} · прогон {result.run_id} · {result.created_at}")
        canvas.drawRightString(page[0] - margin, 7 * mm, f"стр. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(out_path, pagesize=page, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=14 * mm, title=f"{product} — отчёт {result.run_id}")
    el = []
    el.append(Paragraph(rich(f"{product} — отчёт о сверке / 核对报告"), st["h1"]))
    el.append(Paragraph(rich(
        f"Прогон {result.run_id} · сформирован {result.created_at} · запустил: {result.user} · "
        f"{result.duration_sec} c · всего расхождений (ISSUE): {result.total_issues()} · "
        f"качество данных: ошибок {result.dq_counts()['ERROR']}, предупреждений {result.dq_counts()['WARNING']}"),
        st["body"]))
    if not CJK_EMBEDDED:
        el.append(Paragraph("Внимание: в системе не найден шрифт с иероглифами — китайский текст может "
                            "отображаться не во всех программах просмотра.", st["small"]))
    el.append(Spacer(1, 6))

    summary = pd.DataFrame(result.summary_rows())
    if not summary.empty:
        summary["check"] = [labels.check_title(c) for c in summary["check_id"]]
        cols = ["check", "total", "OK", "ISSUE", "REVIEW", "SKIPPED"]
        el.append(Paragraph("Сводка по проверкам / 检查汇总", st["h2"]))
        el.append(_table(summary[cols], st, [labels.bilingual(c) for c in cols],
                         widths=[avail * 0.40] + [avail * 0.12] * 5))

    if result.sources_info:
        src = pd.DataFrame(result.sources_info)
        cols = [c for c in ("label", "file", "rows", "columns", "read_as", "mapped") if c in src.columns]
        el.append(Paragraph("Источники данных / 数据源", st["h2"]))
        el.append(_table(src[cols], st, [labels.bilingual(c) for c in cols], widths=_widths(cols, avail)))

    el.append(Paragraph("Качество данных: где не хватает и что некорректно / 数据质量", st["h2"]))
    if result.findings:
        dq = pd.DataFrame([f.as_dict() for f in result.findings])
        cols = ["severity", "source", "column", "issue", "affected_rows", "examples", "impact"]
        el.append(_table(dq[cols], st, [labels.bilingual(c) for c in cols], max_rows=80, widths=_widths(cols, avail)))
    else:
        el.append(Paragraph("дефектов данных не найдено", st["body"]))

    for cid, out in result.outputs.items():
        df = out.df
        c = out.counts()
        el.append(Paragraph(rich(f"{labels.check_title(cid)} — всего {c['total']}: OK {c['OK']}, "
                                 f"ISSUE {c['ISSUE']}, REVIEW {c['REVIEW']}, SKIPPED {c['SKIPPED']}"), st["h2"]))
        if out.meta.get("summary"):
            el.append(Paragraph(rich(out.meta["summary"]), st["body"]))
        bad = df[df["status"].isin(["ISSUE", "REVIEW"])] if "status" in df.columns else df
        if bad.empty:
            el.append(Paragraph("отклонений нет / 无差异", st["body"]))
            continue
        cols = [x for x in PDF_COLUMNS.get(cid, list(df.columns)) if x in bad.columns]
        el.append(_table(bad[cols], st, [labels.bilingual(x) for x in cols], max_rows=max_rows,
                         widths=_widths(cols, avail)))

    if result.rates_used:
        el.append(Paragraph("Курсы пересчёта валют / 汇率", st["h2"]))
        for r in result.rates_used:
            el.append(Paragraph(rich("• " + r), st["body"]))

    for slot, title in (("ru_dt", "Декларации ДТ РФ (полностью) / 俄罗斯报关单"),
                        ("cn_dec", "Декларации КНР 报关单 (полностью) / 中国报关单")):
        s = result.sources.get(slot)
        if s is not None and not s.df.empty:
            el.append(PageBreak())
            el.append(Paragraph(rich(title), st["h2"]))
            _declarations(el, s, slot, st, avail)

    doc.build(el, onFirstPage=on_page, onLaterPages=on_page)
    return out_path
