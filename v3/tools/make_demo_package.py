#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Демо-пакет SibDoc 3 для пакетной загрузки и проверки отгрузки в реальном времени.

    python3 tools/make_demo_package.py      ->  data/demo/package/Пакет_документов_отгрузка_0000117.zip
                                                 data/demo/package/expected.json (правильные типы — для теста)

Один ZIP с папками, вложенным архивом (в нём ещё один ZIP) и письмом с вложениями — 28 документов
разных форматов: ДТ PDF с текстовым слоем (2 страницы), 报关单 XML и скан JPG, черновики (Excel и CSV в GBK),
инвойс Excel, упаковочный лист Word, CMR PDF, сертификат соответствия JPG, сертификат происхождения TIFF
(2 страницы), контракт и доп. соглашение Word, выгрузка SAP Excel, справочник Excel, выписка банка,
PDF из трёх документов (инвойс на 2 страницах, упаковочный лист, CMR), письмо EML, TXT и файл чертежа
(не поддерживается). Все компании, номера, контейнеры и цены ВЫМЫШЛЕННЫЕ.

Заложенные расхождения (отгрузка 0000117):
  ДТ (скан PDF) ↔ черновик ДТ: строка 1 — вес нетто 47 880 vs 48 880 кг; строка 2 — код 4002201000 vs 4002191300.
  ДТ ↔ система (SAP): строка 2 — цена 9 661 vs 9 761 CNY/т.
  ДТ ↔ справочник: строка 2 — код 4002201000 для SBS L7322 (утверждён 4002191300).
  报关单 (скан JPG) ↔ черновик 报关单: цена 10.157 vs 10.175 CNY/кг.
"""
from __future__ import annotations

import io
import json
import os
import random
import sys
import zipfile
from email.message import EmailMessage
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Font
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))
from make_demo import container, cn_dec_xml, ru_dt_xml  # noqa: E402

OUT = os.path.join(ROOT, "data", "demo", "package")
ZIP_NAME = "Пакет_документов_отгрузка_0000117.zip"
TOP = "Пакет_отгрузки_0000117"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_ZH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
FIXED_DT = (2026, 11, 5, 10, 0, 0)

EXPORTER = "ООО «Пример Экспорт» (вымышленная)"
EXPORTER_EN = "OOO PRIMER EXPORT (fictional)"
IMPORTER = "DEMO IMPORT CO., LTD. (fictional)"
IMPORTER_CN = "示例进口有限公司"
CARRIER = "ООО «Пример Логистик» (вымышленная)"
CONTRACT = "ДЕМО.3001"
C = {k: container("DEMU", n) for k, n in (("A", 201), ("B", 202), ("C", 203), ("D", 211), ("E", 212),
                                          ("F", 221))}
DT117 = "10000010/051126/0000117"
CN117, CN118 = "220220260000000117", "220220260000000118"
ITEMS = [  # отгрузка 0000117
    dict(no=1, name="КАУЧУК БРОМБУТИЛОВЫЙ БИИР-232", name_cn="溴化丁基橡胶", model="Bromobutyl rubber BIIR-232",
         hs="4002391000", hs_draft="4002391000", conts=["A", "B"], net=47880, net_draft=48880, gross=48598.2,
         price=10157, sap_price=10157, material="900001"),
    dict(no=2, name="ТЕРМОЭЛАСТОПЛАСТ СБС Л7322", name_cn="SBS热塑性弹性体", model="SBS L7322",
         hs="4002201000", hs_draft="4002191300", conts=["C"], net=22680, net_draft=22680, gross=23020.2,
         price=9661, sap_price=9761, material="900003"),
]
for it in ITEMS:
    it["value"] = round(it["net"] / 1000 * it["price"], 2)
TOTAL = round(sum(i["value"] for i in ITEMS), 2)


def ru_num(v, nd=0) -> str:
    s = f"{v:,.{nd}f}".replace(",", " ")
    return s.replace(".", ",")


# ------------------------------------------------------------------ Word (.docx без сторонних библиотек)
def _p(text: str, bold=False, size=None) -> str:
    rpr = ""
    if bold or size:
        rpr = "<w:rPr>" + ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "") + "</w:rPr>"
    return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _tbl(rows: list[list[str]]) -> str:
    b = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f"<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/><w:tblBorders>{b}</w:tblBorders></w:tblPr>"]
    for r in rows:
        out.append("<w:tr>" + "".join(f"<w:tc>{_p(str(c))}</w:tc>" for c in r) + "</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def docx(blocks: list) -> bytes:
    """blocks: str (абзац), ("b", str) — жирный заголовок, [[...], ...] — таблица."""
    body = []
    for b in blocks:
        if isinstance(b, list):
            body.append(_tbl(b))
        elif isinstance(b, tuple):
            body.append(_p(b[1], bold=True, size=28))
        else:
            body.append(_p(b))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(body) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": doc,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(zipfile.ZipInfo(name, FIXED_DT), text.encode("utf-8"))
    return buf.getvalue()


# ------------------------------------------------------------------ Excel
def xlsx(sheets: list[tuple[str, list[list]]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
        ws.cell(row=1, column=1).font = Font(bold=True, size=13)
        for col in "ABCDEFGHIJ":
            ws.column_dimensions[col].width = 22
    wb.properties.creator = "SibDoc demo"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ PDF (PyMuPDF, текстовый слой)
class Pdf:
    def __init__(self):
        import pymupdf
        self.m = pymupdf
        self.doc = pymupdf.open()
        self.page = None
        self.y = 0

    def new_page(self):
        self.page = self.doc.new_page(width=595, height=842)
        self.page.insert_font(fontname="dv", fontfile=FONT)
        self.page.insert_font(fontname="dvb", fontfile=FONT_B)
        self.y = 50
        return self

    def text(self, s: str, size=10, bold=False, x=45, gap=None):
        self.page.insert_text((x, self.y), s, fontname="dvb" if bold else "dv", fontsize=size)
        self.y += gap or (size + 6)
        return self

    def table(self, rows: list[list[str]], widths: list[int], size=8):
        x0 = 45
        h = size + 9
        for r in rows:
            x = x0
            for c, w in zip(r, widths):
                self.page.draw_rect(self.m.Rect(x, self.y, x + w, self.y + h), color=(0, 0, 0), width=0.6)
                self.page.insert_text((x + 3, self.y + h - 5), str(c), fontname="dv", fontsize=size)
                x += w
            self.y += h
        self.y += 10
        return self

    def bytes(self) -> bytes:
        self.doc.set_metadata({"title": "SibDoc demo", "author": "SibDoc demo", "creationDate": "D:20261105100000",
                               "modDate": "D:20261105100000", "producer": "SibDoc demo"})
        try:
            self.doc.subset_fonts()          # в PDF — только нужные буквы шрифта (файл в десятки раз меньше)
        except Exception:  # noqa: BLE001
            pass
        return self.doc.tobytes(garbage=3, deflate=True)


# ------------------------------------------------------------------ «сканы»
def _scan_page(title: str, sub: str, rows: list[tuple[str, str]], zh=False, size=(1654, 2339)) -> Image.Image:
    W, H = size
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    body = ImageFont.truetype(FONT_ZH if zh else FONT, 36)
    bold = ImageFont.truetype(FONT_ZH if zh else FONT_B, 46)
    small = ImageFont.truetype(FONT_ZH if zh else FONT, 28)
    d.text((110, 110), title, font=bold, fill=20)
    d.text((110, 180), sub, font=small, fill=70)
    d.line((110, 235, W - 110, 235), fill=40, width=3)
    y = 280
    for label, value in rows:
        d.text((130, y), f"{label}: {value}" if label else value, font=body, fill=15)
        y += 92
    d.text((110, H - 160), "示例：所有编号、公司和价格均为虚构" if zh else "ДЕМО: все номера, компании и даты вымышленные",
           font=small, fill=90)
    return img


def _noisy(img: Image.Image, seed: int) -> Image.Image:
    """Как со сканера: лёгкий поворот, размытие и шум."""
    rnd = random.Random(seed)
    img = img.rotate(rnd.uniform(-0.6, 0.6), resample=Image.BICUBIC, expand=False, fillcolor=245)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    return Image.blend(img, Image.effect_noise(img.size, 40), 0.07)


def jpg(img: Image.Image, q=78) -> bytes:
    buf = io.BytesIO()
    img.convert("L").save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue()


def tiff(pages: list[Image.Image]) -> bytes:
    bw = [p.convert("L").point(lambda v: 255 if v > 150 else 0).convert("1") for p in pages]
    buf = io.BytesIO()
    bw[0].save(buf, "TIFF", compression="group4", save_all=True, append_images=bw[1:], dpi=(200, 200))
    return buf.getvalue()


# ------------------------------------------------------------------ документы
def contract_docx() -> bytes:
    return docx([
        ("b", f"КОНТРАКТ № {CONTRACT} / CONTRACT No. {CONTRACT}"),
        "г. Москва, 10 января 2026 г. / Moscow, January 10, 2026",
        f"{EXPORTER}, именуемое в дальнейшем «Продавец», с одной стороны, и {IMPORTER}, "
        "именуемая в дальнейшем «Покупатель», с другой стороны (вместе — Стороны), заключили настоящий контракт.",
        f"{EXPORTER_EN}, hereinafter referred to as the Seller, and {IMPORTER}, hereinafter referred to as "
        "the Buyer, have concluded this contract.",
        ("b", "1. Предмет контракта / Subject of the contract"),
        "Продавец продаёт, а Покупатель покупает синтетические каучуки (далее — Товар) по спецификациям.",
        ("b", "2. Условия поставки / Terms of delivery"),
        "Условия поставки: CPT ЗАБАЙКАЛЬСК (Инкотермс 2020). Валюта контракта: CNY.",
        ("b", "3. Форс-мажор и арбитраж / Force majeure and arbitration"),
        "Споры рассматриваются в арбитраже по месту нахождения ответчика.",
        ("b", "4. Реквизиты сторон / Details of the parties"),
        [["Продавец / Seller", "Покупатель / Buyer"], [EXPORTER, IMPORTER], ["ИНН 0000000000 (вымышленный)",
                                                                             "USCC 00000000000000000X"]],
        "Подписи сторон / Signatures: ____________ / ____________",
    ])


def addendum_docx() -> bytes:
    return docx([
        ("b", f"ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 01А к контракту № {CONTRACT}"),
        "Supplementary agreement No. 01A to contract No. DEMO.3001",
        "Стороны договорились о поставке партии товара в ноябре 2026 года:",
        [["Марка / Grade", "Количество, т", "Цена, CNY/т"],
         ["Bromobutyl rubber BIIR-232", "47,88", "10 157"], ["SBS L7322", "22,68", "9 661"]],
        "Остальные условия контракта без изменений. Подписи сторон: ____________ / ____________",
    ])


def invoice_xlsx() -> bytes:
    rows = [["COMMERCIAL INVOICE / ИНВОЙС"],
            ["Invoice No", "INV-2026-117"], ["Дата / Date", "2026-11-04"],
            ["Отправитель / экспортёр", EXPORTER], ["Получатель", IMPORTER],
            ["Контракт", f"{CONTRACT}-01А"], ["Условия поставки", "CPT ЗАБАЙКАЛЬСК"], ["Валюта", "CNY"],
            [],
            ["Item No", "Description / Описание товара", "Марка", "HS Code / Код ТН ВЭД", "Container / Контейнер",
             "Net weight, kg / Вес нетто, кг", "Unit price, CNY/t", "Amount, CNY"]]
    for it in ITEMS:
        rows.append([it["no"], it["name"], it["model"], it["hs"], ", ".join(C[c] for c in it["conts"]), it["net"],
                     it["price"], it["value"]])
    rows.append(["Итого / Total", "", "", "", "", sum(i["net"] for i in ITEMS), "", TOTAL])
    rows.append([])
    rows.append(["Terms of payment: 100% в течение 30 дней / within 30 days"])
    return xlsx([("Invoice", rows)])


def packing_docx() -> bytes:
    rows = [["Item No", "Container / Контейнер", "Description / Описание товара", "Places / Мест",
             "Net weight, kg / Вес нетто, кг", "Gross weight, kg / Вес брутто, кг"]]
    for it in ITEMS:
        for c in it["conts"]:
            share = len(it["conts"])
            rows.append([str(it["no"]), C[c], it["name"], "24", ru_num(it["net"] / share, 1),
                         ru_num(it["gross"] / share, 1)])
    return docx([
        ("b", "PACKING LIST / УПАКОВОЧНЫЙ ЛИСТ № PL-2026-117"),
        "Invoice No: INV-2026-117", f"Контракт: {CONTRACT}-01А", f"Отправитель: {EXPORTER}",
        f"Получатель: {IMPORTER}",
        rows,
        "Packages: pallets, 24 per container / паллеты, 24 места на контейнер.",
    ])


def cmr_pdf(pdf: Pdf, no: str, items: list, inv: str, pl: str, dt: str) -> Pdf:
    pdf.new_page()
    pdf.text("CMR", size=16, bold=True)
    pdf.text("МЕЖДУНАРОДНАЯ ТОВАРНО-ТРАНСПОРТНАЯ НАКЛАДНАЯ / INTERNATIONAL CONSIGNMENT NOTE", size=10, bold=True)
    pdf.text(f"CMR № CMR-{no}", size=11, bold=True, gap=22)
    for line in (f"1. Отправитель / Sender: {EXPORTER}", f"2. Получатель / Consignee: {IMPORTER}",
                 "3. Место разгрузки / Place of delivery: Маньчжурия, КНР", "4. Место погрузки / Place of taking over: "
                 "Москва, Россия", f"5. Прилагаемые документы / Documents attached: Invoice {inv}, Packing list {pl}, "
                 f"ДТ {dt}", f"16. Перевозчик / Carrier: {CARRIER}", "Тягач / прицеп: А000АА00 / АА0000 00 (вымышленные)"):
        pdf.text(line, size=9)
    pdf.y += 6
    rows = [["Контейнер / Container", "Описание товара", "Мест", "Вес брутто, кг"]]
    for it in items:
        for c in it["conts"]:
            rows.append([C[c], it["name"], "24", ru_num(it["gross"] / len(it["conts"]), 1)])
    pdf.table(rows, [130, 230, 50, 95])
    pdf.text("23. Подпись перевозчика / Signature of carrier: ____________", size=9)
    pdf.text("ДЕМО: все номера, компании и даты вымышленные", size=8)
    return pdf


def dt_pdf() -> bytes:
    pdf = Pdf()
    for page_no, it in enumerate(ITEMS, start=1):
        pdf.new_page()
        pdf.text("ДЕКЛАРАЦИЯ НА ТОВАРЫ (ЭКСПОРТ)" + (" — ДОБАВОЧНЫЙ ЛИСТ ДТ2" if page_no > 1 else ""), size=14,
                 bold=True)
        pdf.text("Форма ДТ — образец для проверки распознавания (текстовый слой PDF)", size=8)
        pdf.text(f"Номер ДТ: {DT117}", size=11, bold=True, gap=22)
        if page_no == 1:
            for line in (f"1. Отправитель / экспортёр: {EXPORTER}", f"8. Получатель: {IMPORTER}",
                         "14. Декларант: " + EXPORTER, "Таможенная процедура: экспорт (10)",
                         "Таможенный орган: 10000010", "20. Условия поставки: CPT ЗАБАЙКАЛЬСК",
                         "22. Валюта: CNY", f"22. Общая фактурная стоимость: {ru_num(TOTAL, 2)}",
                         f"44. Контракт №: {CONTRACT}-01А", "54. Дата: 05.11.2026"):
                pdf.text(line, size=9)
            pdf.y += 8
        conts = ", ".join(C[c] for c in it["conts"])
        pdf.text(f"Товар № {it['no']}", size=11, bold=True)
        for line in (f"31. Описание товара: {it['name']}", f"Модель: {it['model']}", f"33. Код ТН ВЭД: {it['hs']}",
                     "34. Страна происхождения: RU", f"35. Вес брутто, кг: {ru_num(it['gross'], 1)}",
                     f"38. Вес нетто, кг: {ru_num(it['net'])}", f"42. Фактурная стоимость: {ru_num(it['value'], 2)}",
                     f"Цена за единицу: {ru_num(it['price'], 2)} CNY/т", f"45. Таможенная стоимость: "
                     f"{ru_num(it['value'] * 11.27, 2)} RUB", f"46. Статистическая стоимость: "
                     f"{ru_num(it['value'] / 7.1, 2)} USD", f"31. Контейнеры: {conts}"):
            pdf.text(line, size=9)
        pdf.y += 10
        pdf.text("ДЕМО: все номера, компании и цены вымышленные", size=8)
    return pdf.bytes()


def draft_xlsx() -> bytes:
    rows = [["ЧЕРНОВИК ДЕКЛАРАЦИИ НА ТОВАРЫ (ДТ) — для сверки до подачи"],
            ["Отправитель / экспортёр", EXPORTER], ["Получатель", "DEMO IMPORT CO LTD (fictional)"],
            ["Условия поставки", "CPT"], ["Место поставки", "ЗАБАЙКАЛЬСК"], ["Валюта", "CNY"],
            ["Контракт", f"{CONTRACT}-01А"], [],
            ["Item No", "Описание товара", "Марка", "Код ТН ВЭД", "Контейнер", "Вес брутто, кг", "Вес нетто, кг",
             "Цена за т", "Фактурная стоимость"]]
    for it in ITEMS:
        rows.append([it["no"], it["name"], it["model"], it["hs_draft"], ", ".join(C[c] for c in it["conts"]),
                     it["gross"], it["net_draft"], it["price"], it["value"]])
    return xlsx([("Черновик ДТ", rows)])


def sap_xlsx() -> bytes:
    rows = [["Выгрузка SAP: заказы на поставку (Purchase), ноябрь 2026"],
            ["Shipment date", "Container number", "Material", "Currency", "Main contract", "Add contract",
             "Russian export decl", "Net weight", "Price", "Incoterms"]]
    for it in ITEMS:
        for c in it["conts"]:
            rows.append(["2026-11-05", C[c], it["material"], "CNY", CONTRACT, "01А", "",
                         round(it["net"] / len(it["conts"]) / 1000, 3), it["sap_price"], "CPT"])
    for c in ("D", "E"):
        rows.append(["2026-11-12", C[c], "900001", "CNY", CONTRACT, "02А", "", 24.84, 10157, "CPT"])
    rows.append(["2026-10-20", C["F"], "900001", "CNY", "ДЕМО.1003", "01А", "10000010/201026/0000110", 23.94,
                 10157, "CPT"])
    return xlsx([("Purchase", rows)])


def reference_xlsx() -> bytes:
    rows = [["Справочник: утверждённые коды ТН ВЭД / HS по маркам (мастер-данные, вымышленные)"],
            ["Материал SAP / Material", "Марка / 报关单申报型号", "Код ТН ВЭД / HS Code", "Описание товара",
             "申报要素"],
            ["900001", "Bromobutyl rubber BIIR-232", "4002391000", "Каучук бромбутиловый БИИР-232",
             "4|3|块状|非充油|无品牌|Bromobutyl rubber BIIR-232"],
            ["900002", "Butyl rubber IIR-1675N", "4002311000", "Каучук бутиловый БК-1675Н",
             "4|3|块状|非充油|无品牌|Butyl rubber IIR-1675N"],
            ["900003", "SBS L7322", "4002191300", "Термоэластопласт СБС Л7322", "4|3|颗粒|无品牌|SBS L7322"]]
    return xlsx([("Справочник", rows)])


def bank_xlsx() -> bytes:
    rows = [["ВЫПИСКА ПО СЧЁТУ № 40702156000000000117 (вымышленный)"],
            ["Банк", "АО «Демо Банк» (вымышленный)", "БИК", "044525000"],
            ["Период", "01.11.2026 – 05.11.2026", "Валюта счёта", "CNY"],
            ["Входящий остаток", "0,00"], [],
            ["Дата", "Номер документа", "Дебет", "Кредит", "Контрагент", "Назначение платежа"],
            ["2026-11-05", "117", "", ru_num(TOTAL, 2), IMPORTER, f"Оплата по контракту {CONTRACT}, инвойс INV-2026-117"],
            ["2026-11-05", "118", "150,00", "", "АО «Демо Банк»", "Комиссия за валютный контроль"],
            ["Исходящий остаток", ru_num(TOTAL - 150, 2)]]
    return xlsx([("Выписка", rows)])


def cn_scan_jpg() -> bytes:
    it = ITEMS[0]
    rows = [("海关编号", CN117), ("境内收发货人", IMPORTER_CN), ("合同协议号", f"{CONTRACT}-01А"), ("成交方式", "CPT"),
            ("商品编号", it["hs"]), ("商品名称", it["name_cn"]), ("规格型号", it["model"]), ("净重", f"{it['net']} 千克"),
            ("单价", "10.157"), ("总价", f"{it['value']:.2f}"), ("币制", "CNY"),
            ("集装箱号", ", ".join(C[c] for c in it["conts"])), ("申报日期", "2026-11-06")]
    return jpg(_noisy(_scan_page("中华人民共和国海关出口货物报关单", "报关单扫描件（用于识别测试）", rows, zh=True), 11), 80)


def cn_draft_csv() -> bytes:
    it = ITEMS[0]
    lines = ["报关单草稿 / Черновик 报关单,,,,,,,,,,",
             f"合同协议号,{CONTRACT}-01А,,,,,,,,,",
             "成交方式,CPT,,,,,,,,,",
             ",,,,,,,,,,",
             "项号,集装箱号,商品编号,商品名称,规格型号,成交数量,成交计量单位,单价,总价,币制,净重",
             f"1,{'; '.join(C[c] for c in it['conts'])},{it['hs']},{it['name_cn']},{it['model']},{it['net']},千克,"
             f"10.175,{round(it['net'] * 10.175, 2)},CNY,{it['net']}"]
    return ("\n".join(lines) + "\n").encode("gb18030")


def conformity_jpg() -> bytes:
    rows = [("", "№ ЕАЭС RU С-RU.ДЕМО.В.00117/26"), ("Заявитель", EXPORTER), ("Изготовитель", "АО «Демо Завод» (вымышленное)"),
            ("Продукция", "Каучук бромбутиловый БИИР-232"), ("Код ТН ВЭД ЕАЭС", "4002391000"),
            ("", "Соответствует требованиям ТР ТС 000/2026 (вымышленный)"),
            ("Орган по сертификации", "ООО «Демо Серт» (вымышленный)"), ("Срок действия", "с 01.11.2026 по 31.10.2029")]
    return jpg(_noisy(_scan_page("СЕРТИФИКАТ СООТВЕТСТВИЯ", "ЕВРАЗИЙСКИЙ ЭКОНОМИЧЕСКИЙ СОЮЗ", rows), 12), 76)


def origin_tiff() -> bytes:
    p1 = _scan_page("СЕРТИФИКАТ О ПРОИСХОЖДЕНИИ ТОВАРА", "CERTIFICATE OF ORIGIN · Форма СТ-1 № RU-ДЕМО-0117",
                    [("1. Экспортёр", EXPORTER), ("2. Получатель", IMPORTER), ("3. Средства транспорта", "автомобиль"),
                     ("4. Страна происхождения", "Российская Федерация"), ("5. Контракт", f"{CONTRACT}-01А")])
    p2 = _scan_page("Продолжение. Сведения о товаре", "Goods description (page 2)",
                    [("Товар 1", ITEMS[0]["name"]), ("Товар 2", ITEMS[1]["name"]),
                     ("Количество", "70 560 кг"), ("Уполномоченный орган", "ТПП (вымышленная)")])
    return tiff([p1, p2])


def quality_pdf() -> bytes:
    pdf = Pdf().new_page()
    pdf.text("ПАСПОРТ КАЧЕСТВА / CERTIFICATE OF QUALITY № PQ-0117", size=13, bold=True, gap=24)
    pdf.text("Изготовитель: АО «Демо Завод» (вымышленное)", size=9)
    pdf.text(f"Продукция: {ITEMS[0]['name']} / {ITEMS[0]['model']}", size=9)
    pdf.text(f"Контейнеры: {', '.join(C[c] for c in ITEMS[0]['conts'])}", size=9, gap=20)
    pdf.table([["Показатель", "Норма", "Факт"], ["Вязкость по Муни", "32 ± 4", "33"],
               ["Массовая доля брома, %", "1,8–2,2", "2,0"], ["Летучие вещества, %", "≤ 0,5", "0,2"]], [220, 120, 120])
    pdf.text("Продукция соответствует требованиям технических условий. ДЕМО: данные вымышленные.", size=9)
    return pdf.bytes()


def spec_docx() -> bytes:
    return docx([
        ("b", f"СПЕЦИФИКАЦИЯ № 02 к контракту № {CONTRACT}"),
        "Specification No. 02 to contract No. DEMO.3001",
        [["Марка / Grade", "Количество, т", "Цена, CNY/т", "Условия поставки"],
         ["Bromobutyl rubber BIIR-232", "49,68", "10 157", "CPT ЗАБАЙКАЛЬСК"]],
        "Подписи сторон / Signatures: ____________ / ____________",
    ])


def letter_eml() -> bytes:
    msg = EmailMessage()
    msg["From"] = "ДЕМО Брокер <broker@demo-broker.example>"
    msg["To"] = "customs@primer-export.example"
    msg["Subject"] = "Документы по отгрузке 0000117"
    msg["Date"] = "Thu, 05 Nov 2026 10:00:00 +0300"
    msg.set_content("Добрый день!\n\nВо вложении паспорт качества по отгрузке 0000117 и спецификация № 02 "
                    "к контракту.\n\nС уважением,\nДЕМО Брокер (вымышленный)\n")
    msg.add_attachment(quality_pdf(), maintype="application", subtype="pdf", filename="Паспорт_качества_0117.pdf")
    msg.add_attachment(spec_docx(), maintype="application",
                       subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                       filename="Спецификация_02_к_контракту.docx")
    return bytes(msg)


def set_118_pdf() -> bytes:
    items = [dict(no=1, name="КАУЧУК БРОМБУТИЛОВЫЙ БИИР-232", model="Bromobutyl rubber BIIR-232", hs="4002391000",
                  conts=["D", "E"], net=49680, gross=50425.2, price=10157)]
    it = items[0]
    value = round(it["net"] / 1000 * it["price"], 2)
    pdf = Pdf().new_page()
    pdf.text("COMMERCIAL INVOICE / ИНВОЙС", size=15, bold=True)
    pdf.text("Invoice No: INV-2026-118", size=11, bold=True, gap=20)
    for line in ("Date: 2026-11-11", f"Отправитель / экспортёр: {EXPORTER}", f"Получатель: {IMPORTER}",
                 f"Контракт: {CONTRACT}-02А", "Условия поставки: CPT ЗАБАЙКАЛЬСК", "Валюта: CNY"):
        pdf.text(line, size=9)
    pdf.y += 8
    rows = [["Item No", "Описание товара", "Контейнер", "Вес нетто, кг", "Цена за т", "Amount, CNY"]]
    for c in it["conts"]:
        rows.append(["1", it["name"], C[c], ru_num(it["net"] / 2), ru_num(it["price"]), ru_num(value / 2, 2)])
    pdf.table(rows, [40, 190, 90, 70, 55, 70])
    pdf.text("Продолжение на следующей странице / continued on page 2", size=8)
    pdf.new_page()
    pdf.text("Продолжение инвойса INV-2026-118, стр. 2 / page 2", size=10)
    pdf.y += 6
    pdf.table([["Итого / Total", "", "", ru_num(it["net"]), "", ru_num(value, 2)]], [40, 190, 90, 70, 55, 70])
    pdf.text("Terms of payment: 100% within 30 days / условия оплаты: 100% в течение 30 дней", size=9)
    pdf.text("Банк получателя: АО «Демо Банк» (вымышленный)", size=9)
    pdf.new_page()
    pdf.text("PACKING LIST / УПАКОВОЧНЫЙ ЛИСТ", size=15, bold=True)
    pdf.text("Packing list No: PL-2026-118", size=11, bold=True, gap=20)
    pdf.text("Invoice No: INV-2026-118", size=9)
    rows = [["Контейнер", "Описание товара", "Мест", "Вес нетто, кг", "Вес брутто, кг"]]
    for c in it["conts"]:
        rows.append([C[c], it["name"], "24", ru_num(it["net"] / 2), ru_num(it["gross"] / 2, 1)])
    pdf.table(rows, [100, 200, 50, 80, 85])
    cmr_pdf(pdf, "118", items, "INV-2026-118", "PL-2026-118", "(будет присвоен)")
    return pdf.bytes()


def cover_txt() -> bytes:
    return ("Сопроводительное письмо к пакету документов по отгрузке 0000117\n\n"
            "Добрый день! Направляем пакет документов для проверки до подачи декларации: контракт, инвойс, "
            "упаковочный лист, CMR, сертификаты, черновики деклараций и выгрузку SAP.\n\n"
            "С уважением,\nотдел ВЭД ООО «Пример Экспорт» (вымышленная)\n").encode("utf-8")


def archive_zip() -> bytes:
    old = dict(dt="10000010/201026/0000110", cn="220220260000000110", conts=["F"], contract="ДЕМО.1003-01А",
               hs_ru="4002391000", hs_cn="4002391000", model="Bromobutyl rubber BIIR-232", name_cn="溴化丁基橡胶",
               name_ru="КАУЧУК БРОМБУТИЛОВЫЙ БИИР-232", net=23940, price_t=10157, cur="CNY", cn_price=10.157,
               cn_cur="CNY", terms="CPT", cn_terms="CPT", date_cn="2026-10-21")
    import make_demo
    saved = dict(make_demo.C)
    make_demo.C.update({"F": C["F"]})
    try:
        dt_xml, cn_xml = ru_dt_xml(old), cn_dec_xml(old)
    finally:
        make_demo.C.clear()
        make_demo.C.update(saved)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("报关单_220220260000000110.xml", FIXED_DT), cn_xml)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("2026-10/DT_10000010_201026_0000110.xml", FIXED_DT), dt_xml)
        zf.writestr(zipfile.ZipInfo("2026-10/cn.zip", FIXED_DT), inner.getvalue())
        zf.writestr(zipfile.ZipInfo("2026-10/readme.txt", FIXED_DT),
                    "Архив прошлых отгрузок (октябрь 2026). С уважением, отдел ВЭД.\n".encode("utf-8"))
    return outer.getvalue()


def cn118_xml() -> bytes:
    s = dict(cn=CN118, conts=["D", "E"], contract=f"{CONTRACT}-02А", hs_cn="4002391000", name_cn="溴化丁基橡胶",
             model="Bromobutyl rubber BIIR-232", net=49680, cn_price=10.157, cn_cur="CNY", cn_terms="CPT",
             date_cn="2026-11-12")
    import make_demo
    saved = dict(make_demo.C)
    make_demo.C.update({"D": C["D"], "E": C["E"]})
    try:
        return cn_dec_xml(s)
    finally:
        make_demo.C.clear()
        make_demo.C.update(saved)


# ------------------------------------------------------------------ сборка
def build() -> tuple[bytes, list]:
    t = TOP
    files = [  # (путь в архиве, байты, ожидаемые документы [(страницы, тип)], OCR?)
        (f"{t}/01_Контракт/Контракт_ДЕМО.3001.docx", contract_docx(), [(None, "contract")], False),
        (f"{t}/01_Контракт/Доп_соглашение_01А.docx", addendum_docx(), [(None, "contract")], False),
        (f"{t}/02_Отгрузка_0000117/Invoice_INV-2026-117.xlsx", invoice_xlsx(), [(None, "invoice")], False),
        (f"{t}/02_Отгрузка_0000117/Packing_list_PL-2026-117.docx", packing_docx(), [(None, "packing")], False),
        (f"{t}/02_Отгрузка_0000117/CMR_117.pdf", cmr_pdf(Pdf(), "117", ITEMS, "INV-2026-117", "PL-2026-117",
                                                          DT117).bytes(), [([1, 1], "transport")], False),
        (f"{t}/02_Отгрузка_0000117/Сертификат_соответствия.jpg", conformity_jpg(), [([1, 1], "conformity")], True),
        (f"{t}/02_Отгрузка_0000117/Сертификат_происхождения_0117.tif", origin_tiff(), [([1, 2], "conformity")], True),
        (f"{t}/03_Таможня/ДТ_10000010_051126_0000117.pdf", dt_pdf(), [([1, 2], "ru_dt")], False),
        (f"{t}/03_Таможня/Черновик_ДТ_0000117.xlsx", draft_xlsx(), [(None, "draft")], False),
        (f"{t}/03_Таможня/报关单_扫描件_{CN117}.jpg", cn_scan_jpg(), [([1, 1], "cn_dec")], True),
        (f"{t}/03_Таможня/报关单草稿_0000117.csv", cn_draft_csv(), [(None, "draft")], False),
        (f"{t}/03_Таможня/报关单_{CN118}.xml", cn118_xml(), [(None, "cn_dec")], False),
        (f"{t}/04_Система/SAP_выгрузка_ноябрь_2026.xlsx", sap_xlsx(), [(None, "system_export")], False),
        (f"{t}/04_Система/Справочник_кодов_и_марок.xlsx", reference_xlsx(), [(None, "reference")], False),
        (f"{t}/04_Система/Выписка_по_счёту_ноябрь.xlsx", bank_xlsx(), [(None, "bank")], False),
        (f"{t}/05_Отгрузка_0000118/Комплект_документов_0000118.pdf", set_118_pdf(),
         [([1, 2], "invoice"), ([3, 3], "packing"), ([4, 4], "transport")], False),
        (f"{t}/06_Переписка/Письмо_брокера_0000117.eml", letter_eml(), [(None, "other")], False),
        (f"{t}/06_Переписка/Сопроводительное_письмо.txt", cover_txt(), [(None, "other")], False),
        (f"{t}/07_Архив/Архив_прошлых_отгрузок.zip", archive_zip(), [], False),
        (f"{t}/07_Архив/Чертёж_упаковки.dwg", b"AC1027" + bytes(random.Random(7).randrange(256) for _ in range(600)),
         [(None, "other")], False),
    ]
    expected = []
    for path, _, docs, ocr in files:
        for pages, typ in docs:
            expected.append({"file": f"{ZIP_NAME}/{path}", "pages": pages, "type": typ, "ocr": ocr})
    eml = f"{ZIP_NAME}/{t}/06_Переписка/Письмо_брокера_0000117.eml"
    arc = f"{ZIP_NAME}/{t}/07_Архив/Архив_прошлых_отгрузок.zip"
    expected += [
        {"file": f"{eml}/Паспорт_качества_0117.pdf", "pages": [1, 1], "type": "conformity", "ocr": False},
        {"file": f"{eml}/Спецификация_02_к_контракту.docx", "pages": None, "type": "contract", "ocr": False},
        {"file": f"{arc}/2026-10/DT_10000010_201026_0000110.xml", "pages": None, "type": "ru_dt", "ocr": False},
        {"file": f"{arc}/2026-10/cn.zip/报关单_220220260000000110.xml", "pages": None, "type": "cn_dec", "ocr": False},
        {"file": f"{arc}/2026-10/readme.txt", "pages": None, "type": "other", "ocr": False},
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data, _, _ in files:
            zf.writestr(zipfile.ZipInfo(path, FIXED_DT), data)
    return buf.getvalue(), expected


def main():
    os.makedirs(OUT, exist_ok=True)
    data, expected = build()
    with open(os.path.join(OUT, ZIP_NAME), "wb") as fh:
        fh.write(data)
    with open(os.path.join(OUT, "expected.json"), "w", encoding="utf-8") as fh:
        json.dump({"zip": ZIP_NAME, "documents": expected,
                   "realtime": {"declaration_ru": DT117, "declaration_cn": CN117}}, fh, ensure_ascii=False, indent=1)
    print(f"Демо-пакет: {os.path.join(OUT, ZIP_NAME)} ({len(data) // 1024} КБ), документов: {len(expected)}")


if __name__ == "__main__":
    main()
