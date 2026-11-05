#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Демо-набор из ВЫМЫШЛЕННЫХ данных: ДТ РФ (XML ФТС), 报关单 КНР (XML и Excel с китайскими
заголовками), wide table, Purchase, ручная таблица «SAP vs CD» и пакет документов отгрузки
(инвойсы, упаковочные листы, транспортные документы, сертификаты — data/demo/package/).

    python3 tools/make_demo.py          # -> data/demo/

Расхождения заложены специально (см. data/demo/README.md), чтобы на демо было видно,
как их находит каждая проверка. Реальных номеров, компаний и цен здесь нет.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "data", "demo")


def container(owner: str, serial: int) -> str:
    """Номер контейнера с правильной контрольной цифрой ISO 6346."""
    vals, n = {}, 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if n % 11 == 0:
            n += 1
        vals[ch] = n
        n += 1
    body = f"{owner}{serial:06d}"
    total = sum((vals[ch] if ch.isalpha() else int(ch)) * (2 ** i) for i, ch in enumerate(body))
    return body + str((total % 11) % 10)


C = {k: container("DEMU", i) for i, k in enumerate(["A1", "A2", "B1", "C1", "D1", "D2", "E1", "F1"], start=101)}

# Отгрузки: одна строка = одна ДТ РФ (один товар), её контейнеры и данные трёх сторон.
SHIP = [
    # всё совпадает
    dict(dt="10000010/150926/0000001", cn="220220260000000101", conts=["A1", "A2"], contract="ДЕМО.1001-01А",
         hs_ru="4002391000", hs_cn="4002391000", model="Bromobutyl rubber BIIR-232", name_cn="溴化丁基橡胶",
         name_ru="КАУЧУК БРОМБУТИЛОВЫЙ БИИР-232", net=47880, sap_t=[23.94, 23.94],
         price_t=10157, cur="CNY", cn_price=10.157, cn_cur="人民币", sap_cur="CNY", sap_price=10157,
         terms="CPT", cn_terms="CPT", material="900001", date_cn="2026-09-22"),
    # цена в 报关单 ниже, чем в SAP и в ДТ
    dict(dt="10000010/160926/0000002", cn="220220260000000102", conts=["B1"], contract="ДЕМО.1001-02А",
         hs_ru="4002311000", hs_cn="4002311000", model="Butyl rubber IIR-1675N", name_cn="丁基橡胶",
         name_ru="КАУЧУК БУТИЛОВЫЙ БК-1675Н", net=23940, sap_t=[23.94], price_t=9963, cur="CNY",
         cn_price=9.176, cn_cur="CNY", sap_cur="CNY", sap_price=9963, terms="CPT", cn_terms="CPT",
         material="900002", date_cn="2026-09-23"),
    # разные валюты: ДТ и SAP в USD, 报关单 в юанях — сверка через курс (9.482 CNY/кг ≈ 1405 USD/т
    # по центральному курсу НБК на 24.09.2026; без доступа к курсам — REVIEW «нет курса»)
    dict(dt="10000010/170926/0000003", cn="220220260000000103", conts=["C1"], contract="ДЕМО.1002-01А",
         hs_ru="4002391000", hs_cn="4002391000", model="Bromobutyl rubber BIIR-232", name_cn="溴化丁基橡胶",
         name_ru="КАУЧУК БРОМБУТИЛОВЫЙ БИИР-232", net=24840, sap_t=[24.84], price_t=1405, cur="USD",
         cn_price=9.482, cn_cur="CNY", sap_cur="USD", sap_price=1405, terms="CFR", cn_terms="C&F",
         material="900001", date_cn="2026-09-24"),
    # вес в ДТ больше, чем в SAP и 报关单
    dict(dt="10000010/180926/0000004", cn="220220260000000104", conts=["D1", "D2"], contract="ДЕМО.1002-02А",
         hs_ru="4002391000", hs_cn="4002391000", model="Bromobutyl rubber BIIR-232", name_cn="溴化丁基橡胶",
         name_ru="КАУЧУК БРОМБУТИЛОВЫЙ БИИР-232", net=49500, cn_net=47880, sap_t=[23.94, 23.94],
         price_t=10157, cur="CNY", cn_price=10.157, cn_cur="CNY", sap_cur="CNY", sap_price=10157,
         terms="CPT", cn_terms="CPT", material="900001", date_cn="2026-09-25"),
    # код в ДТ другой марки (первые 6 знаков различаются); контракт в ДТ набран латиницей
    dict(dt="10000010/190926/0000005", cn="220220260000000105", conts=["E1"], contract="ДЕМО.2002-05В",
         contract_ru="DEMO.2002-05B".replace("DEMO", "ДЕМО"), hs_ru="4002201000", hs_cn="4002191300",
         model="SBS L7322", name_cn="SBS热塑性弹性体", name_ru="ТЕРМОЭЛАСТОПЛАСТ СБС Л7322", net=22680,
         sap_t=[22.68], price_t=9661, cur="CNY", cn_price=9.661, cn_cur="CNY", sap_cur="CNY", sap_price=9661,
         terms="CPT", cn_terms="CPT", material="900003", date_cn="2026-09-26"),
    # ДТ есть, а в SAP её нет
    dict(dt="10000010/200926/0000006", cn=None, conts=["F1"], contract="ДЕМО.1003-01А", hs_ru="4002391000",
         hs_cn=None, model="Bromobutyl rubber BIIR-232", name_cn=None, name_ru="КАУЧУК БРОМБУТИЛОВЫЙ БИИР-232",
         net=23940, sap_t=[], price_t=10157, cur="CNY", terms="CPT", material="900001"),
]
CONSIGNOR = "ООО «Пример Экспорт» (вымышленная)"
CONSIGNEE_CN = "示例进口有限公司"
CONSIGNEE_EN = "DEMO IMPORT CO., LTD. (fictional)"


# ================================================================ ВЭД-пакет
# Документы отгрузки к каждой ДТ: инвойс, упаковочный лист, транспортный документ, сертификаты.
# Заложенные расхождения (см. data/demo/README.md):
#   …0000002 — брутто в транспортном документе больше, чем в упаковочном листе;
#   …0000003 — сертификат происхождения: страна KZ, а в ДТ / 报关单 — Россия; инвойс в USD, 报关单 в CNY;
#   …0000004 — нет упаковочного листа; стоимость ДТ больше инвойса (в ДТ завышен вес);
#   …0000005 — сертификат соответствия истёк до даты ДТ;
#   …0000006 — сумма инвойса ≠ ДТ (цена в инвойсе 10 257 вместо 10 157); 报关单 нет.
TR = {  # ДТ -> (вид транспортного документа, номер, код вида документа для гр. 44)
    "10000010/150926/0000001": ("SMGS", "SMGS-26-0001", "02013"),
    "10000010/160926/0000002": ("SMGS", "SMGS-26-0002", "02013"),
    "10000010/170926/0000003": ("B/L", "DEMOBL260003", "02011"),
    "10000010/180926/0000004": ("SMGS", "SMGS-26-0004", "02013"),
    "10000010/190926/0000005": ("CMR", "CMR-DEMO-0005", "02015"),
    "10000010/200926/0000006": ("SMGS", "SMGS-26-0006", "02013"),
}
CERTS = [  # вымышленные сертификаты и декларации
    dict(doc_type="Декларация о соответствии ЕАЭС", cert_no="ЕАЭС N RU Д-RU.ДЕМО01.В.00101/26", issue="2026-01-15",
         expiry="2031-01-14", product="Каучук бромбутиловый", model="BIIR-232", hs="4002", origin="Россия",
         issuer="Испытательный центр «Пример» (вымышленный)", invoice="", dt="10000010/150926/0000001"),
    dict(doc_type="Сертификат происхождения", cert_no="CO-DEMO-26001", issue="2026-09-14", expiry="",
         product="Каучук бромбутиловый", model="BIIR-232", hs="4002391000", origin="Россия",
         issuer="Торговая палата «Пример» (вымышленная)", invoice="INV-DEMO-2601", dt=""),
    dict(doc_type="Сертификат происхождения", cert_no="CO-DEMO-26003", issue="2026-09-16", expiry="",
         product="Каучук бромбутиловый", model="BIIR-232", hs="4002391000", origin="Казахстан",
         issuer="Торговая палата «Пример» (вымышленная)", invoice="INV-DEMO-2603", dt=""),
    dict(doc_type="Сертификат соответствия ЕАЭС", cert_no="ЕАЭС RU С-RU.ДЕМО02.В.00205/24", issue="2024-03-01",
         expiry="2026-08-31", product="Термоэластопласт СБС", model="SBS L7322", hs="4002", origin="Россия",
         issuer="Орган по сертификации «Пример» (вымышленный)", invoice="", dt="10000010/190926/0000005"),
]


def _iso(dt: str, shift: int = 0) -> str:
    d = dt.split("/")[1]
    return (pd.Timestamp(f"20{d[4:6]}-{d[2:4]}-{d[0:2]}") + pd.Timedelta(days=shift)).strftime("%Y-%m-%d")


def _pkg_info() -> dict:
    out = {}
    for i, s in enumerate(SHIP, start=1):
        kind, tr_no, code = TR[s["dt"]]
        inv_price = s["price_t"] + (100 if i == 6 else 0)          # …0000006: цена в инвойсе выше
        net_true = s.get("cn_net", s["net"])                       # …0000004: в ДТ завышен вес
        conts = [(C[c], round(net_true / len(s["conts"]), 1)) for c in s["conts"]]
        out[s["dt"]] = dict(no=i, inv=f"INV-DEMO-260{i}", inv_date=_iso(s["dt"], -2), tr_kind=kind, tr_no=tr_no,
                            tr_code=code, tr_date=_iso(s["dt"], 1), price_t=inv_price, net=net_true, conts=conts,
                            seals={c: f"DS{100000 + i * 100 + k}" for k, (c, _) in enumerate(conts, start=1)})
    return out


PKG = _pkg_info()


def presented_docs_xml(s: dict) -> str:
    p = PKG[s["dt"]]
    docs = [("ИНВОЙС", p["inv"], p["inv_date"], "04021"),
            ({"SMGS": "НАКЛАДНАЯ СМГС", "B/L": "КОНОСАМЕНТ", "CMR": "CMR"}[p["tr_kind"]], p["tr_no"], p["tr_date"],
             p["tr_code"])]
    docs += [(c["doc_type"].upper(), c["cert_no"], c["issue"], "") for c in CERTS if c["dt"] == s["dt"]]
    out = []
    for name, num, date, code in docs:
        code_xml = f"\n        <catESAD_cu:PresentedDocumentModeCode>{code}</catESAD_cu:PresentedDocumentModeCode>" \
            if code else ""
        out.append(f"""      <ESADout_CUPresentedDocument>
        <cat_ru:PrDocumentName>{name}</cat_ru:PrDocumentName>
        <cat_ru:PrDocumentNumber>{num}</cat_ru:PrDocumentNumber>
        <cat_ru:PrDocumentDate>{date}</cat_ru:PrDocumentDate>{code_xml}
      </ESADout_CUPresentedDocument>""")
    return "\n".join(out)


def _dmy(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def _pdf(path: str, title: str, lines: list[str]) -> None:
    """PDF с текстовым слоем (как из программы, не скан)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from app.services.report_pdf import FONT, FONT_B
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont(FONT_B, 14)
    c.drawString(50, 790, title)
    c.setFont(FONT, 10.5)
    y = 760
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 18
    c.save()


def make_package() -> None:
    d = os.path.join(OUT, "package")
    for sub in ("invoice", "packing", "transport", "conformity"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    packing, transport = [], []
    for s in SHIP:
        p = PKG[s["dt"]]
        net = p["net"]
        amount = round(net / 1000 * p["price_t"], 2)
        price_kg = round(p["price_t"] / 1000, 3)
        gross = round(net * 1.015, 1)
        n_pkg = 20 * len(p["conts"])
        terms = f"{s['terms']} ЗАБАЙКАЛЬСК" if s["terms"] != "CFR" else "CFR VLADIVOSTOK"
        name = s["name_ru"]
        # --- инвойс
        if p["no"] == 3:                                       # PDF с текстовым слоем
            _pdf(os.path.join(d, "invoice", f"invoice_{p['inv']}.pdf"), "COMMERCIAL INVOICE / КОММЕРЧЕСКИЙ ИНВОЙС", [
                f"Invoice No: {p['inv']}      Date: {_dmy(p['inv_date'])}",
                f"Seller: {CONSIGNOR}", f"Buyer: {CONSIGNEE_EN}", f"Contract No: {s['contract']}",
                f"Terms of delivery: {terms}", f"Currency: {s['cur']}",
                "Container: " + ", ".join(c for c, _ in p["conts"]),
                f"Description: {s['model']}, HS code {s['hs_ru']}",
                f"Quantity: {net:.0f} kg    Unit price: {s['cur']} {p['price_t']:.2f} per t",
                f"Net weight: {net:,.0f} kg    Gross weight: {gross:,.1f} kg    Packages: {n_pkg}".replace(",", " "),
                f"Total amount: {s['cur']} {amount:,.2f}".replace(",", " ")])
        elif p["no"] == 6:                                     # CSV, шапка блоком
            rows = [["Коммерческий инвойс"], ["№ инвойса:", p["inv"]], ["Дата:", _dmy(p["inv_date"])],
                    ["Продавец:", CONSIGNOR], ["Покупатель:", CONSIGNEE_EN], ["Контракт:", s["contract"]],
                    ["Условия поставки:", terms], ["Валюта:", s["cur"]], [],
                    ["№ п/п", "Наименование", "Марка", "Код ТН ВЭД", "Кол-во", "Ед.", "Цена", "Сумма", "Нетто, кг",
                     "Брутто, кг", "Мест", "Контейнер"],
                    [1, name, s["model"], s["hs_ru"], f"{net:.0f}", "кг", f"{price_kg:.3f}".replace(".", ","),
                     f"{amount:.2f}".replace(".", ","), f"{net:.0f}", f"{gross:.1f}".replace(".", ","), n_pkg,
                     "; ".join(c for c, _ in p["conts"])],
                    ["Итого", "", "", "", f"{net:.0f}", "", "", f"{amount:.2f}".replace(".", ","), f"{net:.0f}",
                     f"{gross:.1f}".replace(".", ","), n_pkg, ""]]
            with open(os.path.join(d, "invoice", f"invoice_{p['inv']}.csv"), "w", encoding="utf-8-sig") as fh:
                fh.write("\n".join(";".join(str(x) for x in r) for r in rows) + "\n")
        else:                                                  # Excel, шапка блоком над таблицей
            grid = [["COMMERCIAL INVOICE / КОММЕРЧЕСКИЙ ИНВОЙС"],
                    ["Invoice No. / № инвойса:", p["inv"], None, "Date / Дата:", _dmy(p["inv_date"])],
                    ["Seller / Продавец:", CONSIGNOR, None, "Buyer / Покупатель:", CONSIGNEE_EN],
                    ["Contract / Контракт:", s["contract"], None, "Terms of delivery / Условия поставки:", terms],
                    ["Currency / Валюта:", s["cur"]], [None],
                    ["№ п/п", "Description / Наименование", "Model / Марка", "HS code / Код ТН ВЭД", "Qty / Кол-во",
                     "Unit / Ед.", "Unit price / Цена за единицу", "Amount / Сумма", "Net weight, kg / Нетто, кг",
                     "Gross weight, kg / Брутто, кг", "Packages / Мест", "Container No. / Контейнер"],
                    [1, name, s["model"], s["hs_ru"], net, "kg", price_kg, amount, net, gross, n_pkg,
                     "; ".join(c for c, _ in p["conts"])],
                    ["Итого / Total", None, None, None, net, None, None, amount, net, gross, n_pkg, None]]
            pd.DataFrame(grid).to_excel(os.path.join(d, "invoice", f"invoice_{p['inv']}.xlsx"), index=False,
                                        header=False, sheet_name="Invoice")
        # --- упаковочный лист (строка на контейнер); …0000004 — упаковочного листа нет
        for c, w in p["conts"]:
            if p["no"] != 4:
                packing.append({"Packing list No": f"PL-DEMO-260{p['no']}", "Invoice No": p["inv"],
                                "Packing list date": _dmy(p["inv_date"]), "Container No": c, "Seal No": p["seals"][c],
                                "Description": name, "Model": s["model"], "Qty": w, "Unit": "kg", "Packages": 20,
                                "Package type": "поддоны / pallets", "Net weight, kg": w,
                                "Gross weight, kg": round(w * 1.015, 1)})
            # --- транспортный документ; …0000002 — брутто больше, чем в упаковочном листе
            if p["tr_kind"] != "B/L":
                transport.append({"Вид документа": {"SMGS": "Накладная СМГС", "CMR": "CMR"}[p["tr_kind"]],
                                  "№ накладной": p["tr_no"], "Дата накладной": _dmy(p["tr_date"]), "Контейнер": c,
                                  "Пломба": p["seals"][c], "Кол-во мест": 20,
                                  "Вес брутто, кг": round(w * 1.015, 1) + (350 if p["no"] == 2 else 0),
                                  "Грузоотправитель": CONSIGNOR, "Грузополучатель": CONSIGNEE_EN})
        if p["tr_kind"] == "B/L":                              # коносамент — PDF с текстовым слоем
            c, w = p["conts"][0]
            _pdf(os.path.join(d, "transport", f"BL_{p['tr_no']}.pdf"), "BILL OF LADING", [
                f"B/L No: {p['tr_no']}      Date: {_dmy(p['tr_date'])}",
                "Shipper: OOO PRIMER EXPORT (fictional)", f"Consignee: {CONSIGNEE_EN}",
                "Port of loading: VLADIVOSTOK      Port of discharge: QINGDAO",
                f"Container: {c}      Seal: {p['seals'][c]}",
                f"Packages: 20 PALLETS      Gross weight: {round(w * 1.015, 1):,.1f} kg".replace(",", " ")])
    pd.DataFrame(packing).to_excel(os.path.join(d, "packing", "packing_lists.xlsx"), index=False,
                                   sheet_name="Packing lists")
    pd.DataFrame(transport).to_excel(os.path.join(d, "transport", "transport_docs.xlsx"), index=False,
                                     sheet_name="Накладные")
    certs = [{"Вид документа": c["doc_type"], "№ сертификата": c["cert_no"], "Дата выдачи": _dmy(c["issue"]),
              "Действителен до": _dmy(c["expiry"]) if c["expiry"] else "", "Продукция": c["product"],
              "Марка": c["model"], "Код ТН ВЭД": c["hs"], "Страна происхождения": c["origin"],
              "№ инвойса": c["invoice"], "Кем выдан": c["issuer"]} for c in CERTS]
    pd.DataFrame(certs).to_excel(os.path.join(d, "conformity", "certificates.xlsx"), index=False,
                                 sheet_name="Сертификаты")


def ru_dt_xml(s: dict) -> bytes:
    customs, d, num = s["dt"].split("/")
    iso = f"20{d[4:6]}-{d[2:4]}-{d[0:2]}"
    value = round(s["net"] / 1000 * s["price_t"], 2)
    conts = "".join(f"<catESAD_cu:ContainerNumber><catESAD_cu:ContainerIdentificaror>{C[c]}"
                    f"</catESAD_cu:ContainerIdentificaror></catESAD_cu:ContainerNumber>" for c in s["conts"])
    contract = s.get("contract_ru") or s["contract"]
    xml = f"""<?xml version="1.0" encoding="windows-1251"?>
<ESADout_CU xmlns="urn:customs.ru:Information:CustomsDocuments:ESADout_CU:5.24.0"
            xmlns:cat_ru="urn:customs.ru:CommonAggregateTypes:5.24.0"
            xmlns:catESAD_cu="urn:customs.ru:CUESADCommonAggregateTypesCust:5.24.0">
  <cat_ru:DocumentID>DEMO-{num}</cat_ru:DocumentID>
  <CustomsProcedure>ЭК</CustomsProcedure>
  <CustomsModeCode>10</CustomsModeCode>
  <cat_ru:GTDID>
    <cat_ru:CustomsCode>{customs}</cat_ru:CustomsCode>
    <cat_ru:RegistrationDate>{iso}</cat_ru:RegistrationDate>
    <cat_ru:GTDNumber>{num}</cat_ru:GTDNumber>
  </cat_ru:GTDID>
  <ESADout_CUGoodsShipment>
    <ESADout_CUConsignor><cat_ru:OrganizationName>{CONSIGNOR}</cat_ru:OrganizationName></ESADout_CUConsignor>
    <ESADout_CUConsignee><cat_ru:OrganizationName>{CONSIGNEE_EN}</cat_ru:OrganizationName></ESADout_CUConsignee>
    <ESADout_CUMainContractTerms>
      <catESAD_cu:ContractCurrencyCode>{s['cur']}</catESAD_cu:ContractCurrencyCode>
      <catESAD_cu:TotalInvoiceAmount>{value}</catESAD_cu:TotalInvoiceAmount>
    </ESADout_CUMainContractTerms>
    <ESADout_CUDeliveryTerms>
      <cat_ru:DeliveryTermsStringCode>{s['terms']}</cat_ru:DeliveryTermsStringCode>
      <cat_ru:DeliveryPlace>ЗАБАЙКАЛЬСК</cat_ru:DeliveryPlace>
    </ESADout_CUDeliveryTerms>
    <ESADout_CUGoods>
      <catESAD_cu:GoodsNumeric>1</catESAD_cu:GoodsNumeric>
      <catESAD_cu:GoodsDescription>{s['name_ru']}</catESAD_cu:GoodsDescription>
      <catESAD_cu:GoodsDescription>МАРКА {s['model']}</catESAD_cu:GoodsDescription>
      <catESAD_cu:GrossWeightQuantity>{round(s['net'] * 1.015, 1)}</catESAD_cu:GrossWeightQuantity>
      <catESAD_cu:NetWeightQuantity>{s['net']}</catESAD_cu:NetWeightQuantity>
      <catESAD_cu:InvoicedCost>{value}</catESAD_cu:InvoicedCost>
      <catESAD_cu:GoodsTNVEDCode>{s['hs_ru']}</catESAD_cu:GoodsTNVEDCode>
      <catESAD_cu:OriginCountryCode>RU</catESAD_cu:OriginCountryCode>
      <ESADout_CUPresentedDocument>
        <cat_ru:PrDocumentName>КОНТРАКТ</cat_ru:PrDocumentName>
        <cat_ru:PrDocumentNumber>{contract}</cat_ru:PrDocumentNumber>
        <cat_ru:PrDocumentDate>2026-01-10</cat_ru:PrDocumentDate>
        <catESAD_cu:PresentedDocumentModeCode>03011</catESAD_cu:PresentedDocumentModeCode>
      </ESADout_CUPresentedDocument>
{presented_docs_xml(s)}      <catESAD_cu:ESADContainer>
        <catESAD_cu:ContainerQuantity>{len(s['conts'])}</catESAD_cu:ContainerQuantity>
        {conts}
      </catESAD_cu:ESADContainer>
    </ESADout_CUGoods>
  </ESADout_CUGoodsShipment>
</ESADout_CU>
"""
    return xml.encode("cp1251")


def cn_dec_xml(s: dict) -> bytes:
    net = s.get("cn_net", s["net"])
    total = round(net * s["cn_price"], 2)
    conts = "".join(f"<Container><ContainerId>{C[c]}</ContainerId><ContainerMd>22G1</ContainerMd>"
                    f"<GoodsNo>1</GoodsNo></Container>" for c in s["conts"])
    trans = {"CIF": "1", "CFR": "2", "C&F": "2", "FOB": "3", "EXW": "7"}.get(s["cn_terms"], s["cn_terms"])
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<DecMessage xmlns="http://www.chinaport.gov.cn/dec" Version="3.1">
  <DecHead>
    <SeqNo>I2026000{s['cn'][-6:]}</SeqNo>
    <EntryId>{s['cn']}</EntryId>
    <IEPort>2218</IEPort>
    <ContrNo>{s['contract']}</ContrNo>
    <TransMode>{trans}</TransMode>
    <NetWt>{net}</NetWt>
    <GrossWt>{round(net * 1.015, 1)}</GrossWt>
    <DDate>{s['date_cn'].replace('-', '')}</DDate>
    <ConsigneeCname>{CONSIGNEE_CN}</ConsigneeCname>
    <ConsignorEname>OOO PRIMER EXPORT (fictional)</ConsignorEname>
    <BillNo>{PKG[s['dt']]['tr_no']}</BillNo>
  </DecHead>
  <DecLists>
    <DecList>
      <GNo>1</GNo>
      <CodeTS>{s['hs_cn']}</CodeTS>
      <GName>{s['name_cn']}</GName>
      <GModel>4|3|块状|非充油|20260801|无品牌|{s['model']}</GModel>
      <GQty>{net}</GQty>
      <GUnit>035</GUnit>
      <DeclPrice>{s['cn_price']}</DeclPrice>
      <DeclTotal>{total}</DeclTotal>
      <TradeCurr>{'142' if s['cn_cur'] in ('人民币', 'CNY') else s['cn_cur']}</TradeCurr>
      <FirstQty>{net}</FirstQty>
      <FirstUnit>035</FirstUnit>
      <OriginCountry>RUS</OriginCountry>
    </DecList>
  </DecLists>
  <DecContainers>{conts}</DecContainers>
</DecMessage>
"""
    return xml.encode("utf-8")


def main():
    os.makedirs(os.path.join(OUT, "ru_dt"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "cn_dec_xml"), exist_ok=True)
    wide, purchase, cn_rows, manual = [], [], [], []
    for s in SHIP:
        with open(os.path.join(OUT, "ru_dt", f"DT_{s['dt'].replace('/', '_')}.xml"), "wb") as fh:
            fh.write(ru_dt_xml(s))
        main_c, add_c = s["contract"].split("-")
        for c, t in zip(s["conts"], s["sap_t"]):
            purchase.append({"Shipment date": f"{s['dt'][9:11]}.{s['dt'][11:13]}.20{s['dt'][13:15]}",
                             "Container number": C[c], "Material": s["material"], "Currency": s["sap_cur"],
                             "Main contract": main_c, "Add contract": add_c, "Russian export decl": s["dt"],
                             "Net weight": t, "Price": s["sap_price"]})
        if not s.get("cn"):
            continue
        with open(os.path.join(OUT, "cn_dec_xml", f"报关单_{s['cn']}.xml"), "wb") as fh:
            fh.write(cn_dec_xml(s))
        net = s.get("cn_net", s["net"])
        cn_rows.append({"海关编号": s["cn"], "项号": 1, "集装箱号": "; ".join(C[c] for c in s["conts"]),
                        "合同协议号": s["contract"], "商品编号": s["hs_cn"], "商品名称": s["name_cn"],
                        "规格型号": f"4|3|块状|非充油|20260801|无品牌|{s['model']}", "成交数量": net,
                        "成交计量单位": "千克", "单价": s["cn_price"], "总价": round(net * s["cn_price"], 2),
                        "币制": s["cn_cur"], "净重": net, "申报日期": s["date_cn"], "成交方式": s["cn_terms"],
                        "境内收发货人": CONSIGNEE_CN, "境外发货人": "OOO PRIMER EXPORT (fictional)",
                        "提运单号": PKG[s["dt"]]["tr_no"]})
        for c in s["conts"]:
            wide.append({"decl_num": s["cn"], "decl_issue_status": "No Issue", "contract": s["contract"],
                         "container_num": C[c], "products_item_number": 1, "product_code": s["hs_cn"],
                         "product_name": s["name_cn"],
                         "products_specs_and_models": f"4|3|块状|非充油|20260801|无品牌|{s['model']}",
                         "products_qty": net, "products_unit": "千克", "products_net_weight": net,
                         "products_price": s["cn_price"], "products_currency": s["cn_cur"],
                         "incoterms": s["cn_terms"], "shipreport_incoterms": s["terms"],
                         "extra_costs_value": "0", "storage_location": "示例保税仓库",
                         "material": s["material"], "main_contract": main_c, "add_contract": add_c,
                         "price": s["sap_price"], "currency_value": s["sap_cur"], "gtd_full": s["dt"],
                         "declaration_dt": s["date_cn"]})
        manual.append({"decl_num": s["cn"] + (" " if s["cn"].endswith("2") else "")})
    manual = [m for m in manual if not m["decl_num"].startswith("220220260000000105")]

    pd.DataFrame(wide).to_excel(os.path.join(OUT, "wide_table_demo.xlsx"), index=False)
    pd.DataFrame(purchase).to_excel(os.path.join(OUT, "purchase_demo.xlsx"), index=False)
    pd.DataFrame(cn_rows).to_excel(os.path.join(OUT, "cn_dec_demo.xlsx"), index=False, sheet_name="报关单")
    pd.DataFrame(manual).to_csv(os.path.join(OUT, "sap_vs_cd_demo.csv"), index=False, sep=";",
                                encoding="cp1251")
    make_package()
    print("Демо-набор:", OUT)
    for k, v in C.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
