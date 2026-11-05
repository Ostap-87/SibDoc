# -*- coding: utf-8 -*-
"""ВЭД-пакет: чтение документов отгрузки (Excel / CSV / PDF / сканы) и проверка «7. Пакет документов»."""
from __future__ import annotations

import glob
import io
import os
import zipfile

import openpyxl
import pandas as pd
import pytest

from app import checks as reg
from app.config_loader import load_rules
from app.models import Source
from app.normalizers import norm_country, norm_doc_no
from app.services import ocr_reader
from app.services import package_docs as pkg
from app.services.loader import LoadError, read_many, read_table

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_DIR = os.path.join(ROOT, "data", "demo", "package")


def _read(paths, slot, cfg):
    files = [(open(p, "rb").read(), os.path.basename(p)) for p in paths]
    df, info = read_many(files, slot, cfg)
    return Source(slot=slot, name=files[0][1], df=df, info=info)


def _xlsx(grid) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(grid).to_excel(buf, index=False, header=False)
    return buf.getvalue()


@pytest.fixture()
def pkg_sources(demo_sources, cfg, demo_dir):
    src = dict(demo_sources)
    for k in ("invoice", "packing", "transport", "conformity"):
        src[k] = _read(sorted(glob.glob(os.path.join(demo_dir, "package", k, "*"))), k, cfg)
    return src


@pytest.fixture()
def fx(offline_rates, monkeypatch):
    import app.checks.docs_package as dp
    monkeypatch.setattr(dp, "fx_convert", offline_rates)
    return offline_rates


def run(src, cfg, **rules_over):
    rules = load_rules("docs_package.yaml")
    rules.update(rules_over)
    return reg.run_check("docs_package", src, rules, cfg)


def pick(out, shipment, level):
    df = out.df
    return df[(df.shipment == shipment) & df.level.str.startswith(level)]


def with_df(src, slot, df):
    out = dict(src)
    out[slot] = Source(slot=slot, name=slot, df=df)
    return out


# ------------------------------------------------------------------ чтение документов
def test_invoice_excel_header_block_and_total_row(cfg):
    grid = [["COMMERCIAL INVOICE / КОММЕРЧЕСКИЙ ИНВОЙС"],
            ["Invoice No. / № инвойса:", "INV-T-1 от 10.09.2026"],
            ["Seller / Продавец:", "ООО «Тест» (вымышленная)", None, "Currency / Валюта:", "CNY"],
            ["Terms of delivery:", "CPT ЗАБАЙКАЛЬСК"], [None],
            ["№ п/п", "Наименование", "Марка", "Кол-во", "Ед.", "Цена", "Сумма", "Нетто, кг", "Брутто, кг", "Мест"],
            [1, "Товар А", "A-1", 1000, "кг", 10, 10000, 1000, 1010, 2],
            [2, "Товар Б", "B-2", 500, "кг", 20, 10000, 500, 505, 1],
            ["Итого", None, None, 1500, None, None, 20000, 1500, 1515, 3]]
    df, info = read_table(_xlsx(grid), "inv.xlsx", "invoice", cfg)
    assert len(df) == 2 and info["table"] and info["header_row"] == 6
    assert set(df.invoice_no) == {"INV-T-1"} and set(df.invoice_date) == {"10.09.2026"}   # «№ … от <дата>»
    assert "unit_price" in df.columns and "sap_price" not in df.columns                    # «Цена» в инвойсе
    assert df.loc[0, "invoice_total"] == 20000 and df.loc[0, "packages_total"] == 3
    assert {"currency", "incoterms", "seller"} <= set(info["header_block"])


def test_packing_csv_gbk_chinese_headers(cfg):
    csv = ("装箱单号,发票号,集装箱号,封号,品名,件数,净重(KG),毛重(KG)\n"
           "PL-1,INV-1,DEMU0001012,S123456,丁基橡胶,20,23940,24299.1\n"
           "PL-1,INV-1,DEMU0001028,S123457,丁基橡胶,20,23940,24299.1\n"
           "合计,,,,,40,47880,48598.2\n").encode("gb18030")
    df, info = read_table(csv, "pl.csv", "packing", cfg)
    assert info["encoding"] == "gb18030" and len(df) == 2
    assert {"packing_no", "invoice_no", "container", "seal_no", "packages", "net_weight", "gross_weight"} <= set(df.columns)
    assert info["totals"] == ["gross_weight_total", "net_weight_total", "packages_total"]


def test_transport_tonnes_and_title_doc_type(cfg):
    grid = [["Накладная СМГС"], ["№ накладной", "Дата накладной", "Контейнер", "Пломба", "Мест", "Брутто, т"],
            ["SMGS-1", "2026-09-12", "DEMU0001012", "S1", 20, 24.3]]
    df, info = read_table(_xlsx(grid), "tr.xlsx", "transport", cfg)
    r = df.iloc[0]
    assert r.bill_of_lading == "SMGS-1" and r.doc_type == "SMGS" and r.gross_weight == 24300
    assert info["converted_t_to_kg"] == ["gross_weight"]


def test_slot_units_in_tonnes(cfg):
    import copy
    c2 = copy.deepcopy(cfg)
    c2["sources"]["transport"]["units"] = {"gross_weight": "t"}
    grid = [["№ накладной", "Контейнер", "Брутто"], ["SMGS-1", "DEMU0001012", 24.3]]
    df, _ = read_table(_xlsx(grid), "tr.xlsx", "transport", c2)
    assert df.iloc[0].gross_weight == 24300
    df, _ = read_table(_xlsx(grid), "tr.xlsx", "transport", cfg)
    assert df.iloc[0].gross_weight == 24.3


def test_invoice_pdf_text_layer(cfg):
    f = os.path.join(PKG_DIR, "invoice", "invoice_INV-DEMO-2603.pdf")
    df, info = read_table(open(f, "rb").read(), os.path.basename(f), "invoice", cfg)
    r = df.iloc[0]
    assert info["format"].startswith("pdf") and info["detected_kind"] == "invoice"
    assert r.invoice_no == "INV-DEMO-2603" and r.currency == "USD" and float(r.invoice_total) == 34900.2
    assert r.container == "DEMU0001049" and r.incoterms.startswith("CFR") and float(r.net_weight_total) == 24840


@pytest.mark.parametrize("text,dtype,number", [
    ("CMR\nCMR No: CMR-77-001\nGross weight: 24 300 kg", "CMR", "CMR-77-001"),
    ("BILL OF LADING\nB/L No: COSU1234567\nContainer: DEMU0001012 Seal: AB12345", "B/L", "COSU1234567"),
    ("Накладная СМГС\nСМГС № 26-123456\nКонтейнер DEMU0001012", "SMGS", "26-123456"),
    ("AIR WAYBILL\nAWB No: 123-45678901\nPackages: 4", "AWB", "123-45678901"),
    ("提单\n提单号：DEMOBL0001\n集装箱 DEMU0001012", "B/L", "DEMOBL0001"),
])
def test_transport_text_doc_types(text, dtype, number):
    f = pkg.parse_text_fields(text, "transport")
    assert f["doc_type"]["value"] == dtype and f["bill_of_lading"]["value"] == number


def test_certificate_texts():
    t = ("СЕРТИФИКАТ СООТВЕТСТВИЯ\nЕАЭС RU С-RU.ДЕМО02.В.00205/24\nДата регистрации: 01.03.2024\n"
         "Действителен до 31.08.2026\nКод ТН ВЭД ЕАЭС: 4002\nМарка: SBS L7322\n")
    f = pkg.parse_text_fields(t, "conformity")
    assert f["doc_type"]["value"] == "EAEU-CERT" and f["cert_no"]["value"].startswith("ЕАЭС RU С-RU.ДЕМО02")
    assert (f["issue_date"]["value"], f["expiry_date"]["value"], f["hs_code"]["value"]) == ("01.03.2024", "31.08.2026", "4002")
    co = pkg.parse_text_fields("CERTIFICATE OF ORIGIN\nCertificate No: CO-1\nCountry of origin: Russia\n"
                               "Invoice No: INV-1\nDate of issue: 2026-09-14", "conformity")
    assert co["doc_type"]["value"] == "CO" and co["origin_country"]["value"] == "Russia"
    assert co["invoice_no"]["value"] == "INV-1" and co["issue_date"]["value"] == "2026-09-14"
    assert pkg.parse_text_fields("CERTIFICATE OF ORIGIN FORM E\nNo: E1", "conformity")["doc_type"]["value"] == "FORM-E"


def test_detect_kind_by_title():
    assert pkg.detect_kind("COMMERCIAL INVOICE\nInvoice No: 1") == "invoice"
    assert pkg.detect_kind("Упаковочный лист № 5\nИнвойс № 1") == "packing"
    assert pkg.detect_kind("CMR\nInternational consignment note") == "transport"
    assert pkg.detect_kind("原产地证书\nCERTIFICATE OF ORIGIN") == "conformity"
    assert pkg.detect_kind("Договор поставки") is None


def test_conformity_register_and_doc_types(cfg):
    src = _read([os.path.join(PKG_DIR, "conformity", "certificates.xlsx")], "conformity", cfg)
    assert list(src.df.doc_type) == ["EAEU-DECL", "CO", "CO", "EAEU-CERT"]
    assert {"cert_no", "issue_date", "expiry_date", "hs_code", "origin_country", "invoice_no"} <= set(src.df.columns)


def test_dt_presented_documents(cfg, demo_dir):
    src = _read(sorted(glob.glob(os.path.join(demo_dir, "ru_dt", "*.xml"))), "ru_dt", cfg)
    docs = src.df.set_index("decl_ru")["presented_docs"]
    assert "04021 INV-DEMO-2601" in docs["10000010/150926/0000001"]
    assert "SMGS-26-0001" in docs["10000010/150926/0000001"] and "ЕАЭС N RU Д-RU" in docs["10000010/150926/0000001"]


def test_zip_with_pdf_into_package_slot(cfg):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in glob.glob(os.path.join(PKG_DIR, "invoice", "*")):
            z.write(f, "inv/" + os.path.basename(f))
    df, info = read_table(buf.getvalue(), "inv.zip", "invoice", cfg)
    assert df["invoice_no"].nunique() == 6 and len(info["members"]) == 6


def test_pdf_only_in_package_slots(cfg):
    f = os.path.join(PKG_DIR, "invoice", "invoice_INV-DEMO-2603.pdf")
    with pytest.raises(LoadError):
        read_table(open(f, "rb").read(), "x.pdf", "wide_table", cfg)


def test_normalizers():
    assert norm_doc_no("№ INV-2026/001") == norm_doc_no("inv 2026-001") == "INV2026001"
    assert norm_country("Россия") == norm_country("RUS") == norm_country("俄罗斯") == "RU"
    assert norm_country("Kazakhstan") == "KZ" and norm_country("中国") == "CN"


@pytest.mark.skipif(not ocr_reader.tesseract_available(), reason="нет tesseract")
def test_transport_scan_image_ocr(cfg):
    from PIL import Image, ImageDraw, ImageFont
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path):
        pytest.skip("нет шрифта DejaVu")
    img = Image.new("RGB", (760, 200), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 26)
    for i, line in enumerate(["CMR", "CMR No: CMR-TEST-0001", "Container: DEMU0001012"]):
        d.text((24, 16 + i * 58), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    df, info = read_table(buf.getvalue(), "cmr.png", "transport", cfg)
    assert info["format"] == "OCR" and df.iloc[0]["bill_of_lading"] == "CMR-TEST-0001"
    assert df.iloc[0]["doc_type"] == "CMR"


# ------------------------------------------------------------------ проверка на демо-наборе
def test_demo_package(pkg_sources, cfg, fx):
    out = run(pkg_sources, cfg)
    c = out.counts()
    assert (c["total"], c["OK"], c["ISSUE"], c["REVIEW"]) == (37, 28, 7, 2)
    assert len(out.meta["shipments"]) == 6
    assert set(pick(out, "INV-DEMO-2601", "").status) == {"OK"}                     # всё сходится
    gross = pick(out, "INV-DEMO-2602", "Упаковочный лист")
    assert gross.iloc[0].status == "ISSUE" and "брутто" in gross.iloc[0].detail and "毛重" in gross.iloc[0].detail_zh
    origin = pick(out, "INV-DEMO-2603", "Сертификат: сертификат происхождения").iloc[0]
    assert origin.status == "ISSUE" and "KZ" in origin.detail
    fxrow = pick(out, "INV-DEMO-2603", "Инвойс ↔ 报关单").iloc[0]
    assert fxrow.status == "REVIEW" and "валюты разные" in fxrow.detail and fxrow.fx_rate
    st = pick(out, "INV-DEMO-2604", "Комплект").iloc[0]
    assert st.status == "ISSUE" and "упаковочный лист" in st.detail and "装箱单" in st.detail_zh
    assert pick(out, "INV-DEMO-2604", "Инвойс ↔ ДТ").iloc[0].status == "ISSUE"     # в ДТ завышен вес -> стоимость
    cert = pick(out, "INV-DEMO-2605", "Сертификат").iloc[0]
    assert cert.status == "ISSUE" and "истёк" in cert.detail
    assert pick(out, "INV-DEMO-2606", "Инвойс ↔ ДТ").iloc[0].status == "ISSUE"
    s6 = pick(out, "INV-DEMO-2606", "Комплект").iloc[0]
    assert s6.status == "REVIEW" and "报关单" in s6.detail                           # ожидается, но нет


def test_skipped_without_package(demo_sources, cfg):
    out = run(demo_sources, cfg)
    assert list(out.df.status) == ["SKIPPED"]


def test_required_and_expected_docs(pkg_sources, cfg, fx):
    out = run(pkg_sources, cfg, required_docs=["invoice", "packing", "transport", "ru_dt", "conformity"],
              expected_docs=[])
    sets = out.df[out.df.level.str.startswith("Комплект")].set_index("shipment").status
    assert sets["INV-DEMO-2602"] == "ISSUE" and sets["INV-DEMO-2606"] == "ISSUE" and sets["INV-DEMO-2601"] == "OK"


def test_not_loaded_kind(pkg_sources, cfg, fx):
    src = dict(pkg_sources)
    src.pop("packing")
    sets = run(src, cfg).df.query("level == 'Комплект документов'")
    assert set(sets.status) >= {"REVIEW"} and sets.detail.str.contains("не загружены").all()
    sets = run(src, cfg, not_loaded="issue").df.query("level == 'Комплект документов'")
    assert set(sets.status) == {"ISSUE"}


def test_invoice_packing_branches(pkg_sources, cfg, fx):
    pk = pkg_sources["packing"].df.copy()
    i = pk.index[pk.invoice_no == "INV-DEMO-2601"]
    pk.loc[i[0], ["qty", "net_weight", "packages"]] = [23000, 23000, 17]
    extra = pk.loc[i[0]].copy()
    extra[["model", "qty", "net_weight", "gross_weight", "packages"]] = ["OTHER-1", 10, 10, 11, 1]
    pk = pd.concat([pk, extra.to_frame().T], ignore_index=True)
    r = pick(run(with_df(pkg_sources, "packing", pk), cfg), "INV-DEMO-2601", "Инвойс ↔ упаковочный лист").iloc[0]
    assert r.status == "ISSUE"
    for word in ("позиции", "нет в инвойсе: OTHER-1", "количество", "нетто", "места"):
        assert word in r.detail
    assert "брутто: ✓" in r.checks or "брутто: ✗" in r.checks


def test_invoice_decl_currency_terms_reference(pkg_sources, cfg, fx):
    inv = pkg_sources["invoice"].df.copy()
    k = inv.invoice_no == "INV-DEMO-2601"
    inv.loc[k, "incoterms"] = "FCA МОСКВА"
    r = pick(run(with_df(pkg_sources, "invoice", inv), cfg), "INV-DEMO-2601", "Инвойс ↔ ДТ").iloc[0]
    assert r.status == "ISSUE" and "условия поставки" in r.detail
    r = pick(run(with_df(pkg_sources, "invoice", inv), cfg, incoterms_mismatch="review"), "INV-DEMO-2601",
             "Инвойс ↔ ДТ").iloc[0]
    assert r.status == "REVIEW"
    # валюта инвойса другая -> пересчёт по курсу (ДТ в CNY)
    inv2 = pkg_sources["invoice"].df.copy()
    k2 = inv2.invoice_no == "INV-DEMO-2601"
    inv2.loc[k2, "currency"] = "USD"
    inv2.loc[k2, "invoice_total"] = round(486317.16 / 6.7489, 2)
    r = pick(run(with_df(pkg_sources, "invoice", inv2), cfg), "INV-DEMO-2601", "Инвойс ↔ ДТ").iloc[0]
    assert r.status == "REVIEW" and "валюты разные" in r.detail
    inv2.loc[k2, "invoice_total"] = 1000
    r = pick(run(with_df(pkg_sources, "invoice", inv2), cfg), "INV-DEMO-2601", "Инвойс ↔ ДТ").iloc[0]
    assert r.status == "ISSUE"
    # номер инвойса не указан в ДТ: связь по контейнерам, ссылка — REVIEW / ISSUE / off
    dt = pkg_sources["ru_dt"].df.copy()
    j = dt.decl_ru == "10000010/150926/0000001"
    dt.loc[j, "presented_docs"] = dt.loc[j, "presented_docs"].str.replace("04021 INV-DEMO-2601; ", "", regex=False)
    src = with_df(pkg_sources, "ru_dt", dt)
    r = pick(run(src, cfg), "INV-DEMO-2601", "Инвойс ↔ ДТ").iloc[0]
    assert r.status == "REVIEW" and "не найден среди документов ДТ" in r.detail and r.decl_ru
    assert pick(run(src, cfg, reference_missing="issue"), "INV-DEMO-2601", "Инвойс ↔ ДТ").iloc[0].status == "ISSUE"
    assert pick(run(src, cfg, reference_missing="off"), "INV-DEMO-2601", "Инвойс ↔ ДТ").iloc[0].status == "OK"


def test_packing_transport_branches(pkg_sources, cfg, fx):
    assert pick(run(pkg_sources, cfg, gross_tolerance_pct=2), "INV-DEMO-2602", "Упаковочный лист").iloc[0].status == "OK"
    tr = pkg_sources["transport"].df.copy()
    i = tr.index[tr.bill_of_lading == "SMGS-26-0001"]
    tr.loc[i[0], "seal_no"] = "XX000000"
    tr.loc[i[1], "container"] = "DEMU0001093"
    r = pick(run(with_df(pkg_sources, "transport", tr), cfg), "INV-DEMO-2601", "Упаковочный лист").iloc[0]
    assert r.status == "ISSUE" and "пломбы" in r.detail and "контейнеры" in r.detail and "封号" in r.detail_zh


def test_transport_reference_in_declarations(pkg_sources, cfg, fx):
    tr = pkg_sources["transport"].df.copy()
    tr.loc[tr.bill_of_lading == "SMGS-26-0002", "bill_of_lading"] = "SMGS-26-9999"
    r = pick(run(with_df(pkg_sources, "transport", tr), cfg), "INV-DEMO-2602", "Транспортный документ").iloc[0]
    assert r.status == "REVIEW" and "гр. 44" in r.detail and "提运单号" in r.detail


def _cert_src(pkg_sources, **row):
    base = {"doc_type": "Сертификат соответствия ЕАЭС", "cert_no": "ЕАЭС RU С-RU.ТЕСТ.В.00001/26",
            "issue_date": "01.01.2026", "expiry_date": "31.12.2028", "hs_code": "4002", "invoice_no": "INV-DEMO-2601"}
    base.update(row)
    return with_df(pkg_sources, "conformity", pd.DataFrame([base]))


def test_certificate_branches(pkg_sources, cfg, fx):
    level = "Сертификат"
    assert pick(run(_cert_src(pkg_sources), cfg), "INV-DEMO-2601", level).iloc[0].status == "OK"
    r = pick(run(_cert_src(pkg_sources, issue_date="20.09.2026"), cfg), "INV-DEMO-2601", level).iloc[0]
    assert r.status == "REVIEW" and "позже" in r.detail
    r = pick(run(_cert_src(pkg_sources, issue_date="20.09.2026"), cfg, issued_after_decl="issue"), "INV-DEMO-2601",
             level).iloc[0]
    assert r.status == "ISSUE"
    r = pick(run(_cert_src(pkg_sources, issue_date="", expiry_date=""), cfg), "INV-DEMO-2601", level).iloc[0]
    assert r.status == "REVIEW" and "нет дат" in r.detail
    r = pick(run(_cert_src(pkg_sources, hs_code="3902"), cfg), "INV-DEMO-2601", level).iloc[0]
    assert r.status == "ISSUE" and "код сертификата 3902" in r.detail
    assert pick(run(_cert_src(pkg_sources, hs_code="3902"), cfg, hs_mismatch="review"), "INV-DEMO-2601",
                level).iloc[0].status == "REVIEW"
    # код длиннее начала кода товара: совпадение по первым N знакам (hs_prefix_len)
    src = _cert_src(pkg_sources, hs_code="4002399000")
    assert pick(run(src, cfg, hs_prefix_len=6), "INV-DEMO-2601", level).iloc[0].status == "OK"
    assert pick(run(src, cfg, hs_prefix_len=7), "INV-DEMO-2601", level).iloc[0].status == "ISSUE"
    # страна в сертификате происхождения
    co = _cert_src(pkg_sources, doc_type="Сертификат происхождения", origin_country="Китай", expiry_date="")
    r = pick(run(co, cfg), "INV-DEMO-2601", level).iloc[0]
    assert r.status == "ISSUE" and "страна происхождения" in r.detail and "原产国" in r.detail_zh
    assert pick(run(co, cfg, origin_mismatch="review"), "INV-DEMO-2601", level).iloc[0].status == "REVIEW"
    co_ok = _cert_src(pkg_sources, doc_type="СТ-1", origin_country="Российская Федерация", expiry_date="")
    assert pick(run(co_ok, cfg), "INV-DEMO-2601", level).iloc[0].status == "OK"


def test_certificate_linking(pkg_sources, cfg, fx):
    free = _cert_src(pkg_sources, invoice_no="", cert_no="CERT-FREE-1")
    out = run(free, cfg)
    r = out.df[out.df.cert_no == "CERT-FREE-1"].iloc[0]
    assert r.status == "REVIEW" and r.shipment == "—" and "не связан" in r.detail
    out = run(free, cfg, link_by_hs=True)                      # код 4002 подходит ко всем отгрузкам каучука
    linked = out.df[(out.df.cert_no == "CERT-FREE-1") & out.df.level.str.startswith("Сертификат")]
    assert len(linked) == 6 and "—" not in set(linked.shipment)


def test_group_by_rules(pkg_sources, cfg, fx):
    base = len(run(pkg_sources, cfg).meta["shipments"])
    by_ctn = run(pkg_sources, cfg, group_by=["container"])
    assert len(by_ctn.meta["shipments"]) == base
    by_inv = run(pkg_sources, cfg, group_by=["invoice_no"])
    # без контейнеров накладные и 报关单 (без ссылки на инвойс) — отдельные «отгрузки» без инвойса
    assert len(by_inv.meta["shipments"]) > base
    assert by_inv.df.shipment.str.startswith("конт.").any()


def test_container_reuse_nearest_date(cfg):
    from app.checks import docs_package as dp

    def doc(kind, number, date, conts, invoices=()):
        return {"kind": kind, "number": number, "key": norm_doc_no(number), "date": date, "containers": conts,
                "invoices": set(invoices), "refs": set(), "invoice_numbers": list(invoices)}
    docs = [doc("invoice", "INV-A", "2026-01-10", ["DEMU0001012"], ["INVA"]),
            doc("invoice", "INV-B", "2026-06-10", ["DEMU0001012"], ["INVB"]),
            doc("transport", "SMGS-B", "2026-06-12", ["DEMU0001012"]),
            doc("transport", "SMGS-OLD", "2025-01-01", ["DEMU0001012"])]
    groups = dp.build_shipments(docs, {"group_by": ["invoice_no", "container"], "container_match_days": 120})
    by = {tuple(sorted(d["number"] for d in g)) for g in groups}
    assert ("INV-B", "SMGS-B") in by and ("INV-A",) in by and ("SMGS-OLD",) in by


def test_dq_container_check_digit_in_package(pkg_sources, cfg):
    from app.services import data_quality
    tr = pkg_sources["transport"].df.copy()
    tr.loc[tr.index[0], "container"] = "DEMU0001013"
    f = data_quality.analyze(with_df(pkg_sources, "transport", tr), cfg)
    assert any(x.source == "transport" and "ISO 6346" in x.issue and "Пакет" in x.impact for x in f)


# ------------------------------------------------------------------ отчёты и API
def test_reports_package_sheets(pkg_sources, cfg, fx, tmp_path):
    from app.services.engine import run as engine_run
    from app.services.report_excel import write_excel
    from app.services.report_pdf import write_pdf
    res = engine_run(pkg_sources, cfg)
    wb = openpyxl.load_workbook(write_excel(res, cfg, str(tmp_path / "r.xlsx")))
    assert {"7 Пакет документов", "Инвойсы 商业发票", "Уп. листы 装箱单", "Трансп. документы 运输单据",
            "Сертификаты 证书"} <= set(wb.sheetnames)
    ws = wb["7 Пакет документов"]
    hdr = [c.value for c in ws[1]]
    assert "Отгрузка / 货物批次" in hdr and "Пояснение (中文) / 说明（中文）" in hdr
    assert any(r == "курс USD/CNY = 6.7489 (тест, 2026-09-24)" for r in res.rates_used)
    import pymupdf
    text = "".join(p.get_text() for p in pymupdf.open(write_pdf(res, cfg, str(tmp_path / "r.pdf"))))
    assert "Пакет документов ВЭД по отгрузкам" in text and "INV-DEMO-2604" in text and "装箱单" in text


@pytest.fixture()
def client(isolated_storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.server as server
    monkeypatch.setattr(server, "UPLOADS_DIR", str(tmp_path / "uploads"))
    server._uploads.clear()
    server._scans.clear()
    return TestClient(server.app), server


def test_api_package_upload_cards_run(client, fx):
    c, server = client
    conf = c.get("/api/config").json()["sources"]
    assert conf["invoice"]["package"] == "invoice" and ".pdf" in conf["invoice"]["accept"]
    assert ".pdf" not in conf["wide_table"]["accept"]
    files = [("files", (os.path.basename(f), open(f, "rb").read()))
             for f in sorted(glob.glob(os.path.join(PKG_DIR, "invoice", "*")))]
    r = c.post("/api/upload/invoice", files=files)
    assert r.status_code == 200 and r.json()["rows"] == 6
    d = c.get("/api/package-docs/invoice").json()
    assert d["count"] == 6 and {x["number"] for x in d["documents"]} >= {"INV-DEMO-2601", "INV-DEMO-2603"}
    doc = next(x for x in d["documents"] if x["number"] == "INV-DEMO-2601")
    assert doc["lines"] and any(h["key"] == "currency" for h in doc["header"])
    assert c.get("/api/package-docs/wide_table").status_code == 400
    # после перезапуска PDF тоже восстанавливается из папки слота
    server._uploads.clear()
    server._restore_uploads()
    assert server._uploads["invoice"].rows == 6
    run_res = c.post("/api/run").json()
    assert "docs_package" in run_res["checks"]
    cols = [x["key"] for x in run_res["checks"]["docs_package"]["columns"]]
    assert "detail_zh" in cols and "shipment" in cols


def test_api_rules_change_logged(client):
    c, server = client
    r = c.get("/api/rules/docs_package.yaml").json()["rules"]
    r["gross_tolerance_pct"] = 2
    d = c.put("/api/rules/docs_package.yaml", json=r).json()
    assert d["diff"] == [{"key": "gross_tolerance_pct", "old": 0.5, "new": 2}]
    e = c.get("/api/journal?kind=rules_change").json()["entries"][0]
    assert "docs_package.yaml" in e["detail"] and "gross_tolerance_pct" in e["detail"]


def test_api_scans_package_document(client):
    c, server = client
    f = os.path.join(PKG_DIR, "transport", "BL_DEMOBL260003.pdf")
    r = c.post("/api/scans/upload", files=[("files", ("BL.pdf", open(f, "rb").read()))]).json()
    rec = r["results"][0]
    assert rec["package_kind"] == "transport" and rec["fields"]["bill_of_lading"]["value"] == "DEMOBL260003"
    assert rec["fields"]["doc_type"]["value"] == "B/L"
    added = c.post("/api/scans/to-source").json()["added"]
    assert added == {"transport": 1}
    assert server._uploads["transport"].df.iloc[0]["container"] == "DEMU0001049"
    x = c.get("/api/scans/excel")
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    assert "TRANSPORT_DOC" in wb.sheetnames
    prev = c.get("/api/scans/preview").json()
    assert prev["rows"][0]["kind"] == "transport" and "bill_of_lading" in prev["labels"]
