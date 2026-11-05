# -*- coding: utf-8 -*-
"""SibDoc 3: пакетная загрузка — форматы, деление PDF, классификация (точность на демо-пакете),
поля и источники, ручная правка типа, «Пакет.xlsx», API, работа без сети."""
from __future__ import annotations

import io
import os
import socket
import sys
import zipfile
from email.message import EmailMessage

import openpyxl
import pandas as pd
import pytest

from app.intake import classify, formats, pipeline
from app.intake import package as pkg

from _intake_demo import ROOT, expected, use_demo_storage

sys.path.insert(0, os.path.join(ROOT, "tools"))
from make_demo_package import Pdf, docx  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "intake")
needs_ocr = pytest.mark.skipif(not formats.ocr_available(), reason="нет tesseract")


def _docs(data: bytes, name: str) -> list[dict]:
    ctx = formats.ReadContext()
    n = [0]

    def nid():
        n[0] += 1
        return f"d{n[0]:03d}"
    out = []
    for part in formats.read_any(data, name, ctx):
        out += pipeline.documents_from_part(part, nid)
    return out


def _pdf(pages: list[list[str]]) -> bytes:
    pdf = Pdf()
    for lines in pages:
        pdf.new_page()
        for i, ln in enumerate(lines):
            pdf.text(ln, size=13 if i == 0 else 9, bold=i == 0)
    return pdf.bytes()


def _xlsx(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(xw, sheet_name=name, index=False, header=False)
    return buf.getvalue()


INVOICE_P1 = ["COMMERCIAL INVOICE / ИНВОЙС", "Invoice No: INV-1", "Отправитель / экспортёр: ООО «Альфа» (вымышленная)",
              "Контракт: ДЕМО.9-01", "Условия поставки: CPT ЗАБАЙКАЛЬСК", "Terms of payment: 30 days"]
INVOICE_P2 = ["Продолжение, стр. 2", "Итого: 1 000,00", "Total amount: 1 000,00 CNY"]
PACKING = ["PACKING LIST / УПАКОВОЧНЫЙ ЛИСТ", "Packing list No: PL-1", "Количество мест: 24 pallets",
           "Вес брутто, кг: 24 300", "Вес нетто, кг: 23 940"]


# ------------------------------------------------------------------ форматы
def test_pdf_with_several_documents_is_split_by_pages():
    docs = _docs(_pdf([INVOICE_P1, INVOICE_P2, PACKING]), "set.pdf")
    assert [(d["type"], d["pages"]) for d in docs] == [("invoice", [1, 2]), ("packing", [3, 3])]
    assert "заголовок" in docs[1]["split"]["reason"] and docs[1]["split"]["of"] == 2
    assert docs[0]["fields"]["contract"]["value"] == "ДЕМО.9-01"
    assert docs[0]["fields"]["contract"]["source"]["page"] == 1


def test_pdf_same_type_with_other_number_is_a_new_document():
    inv2 = [x.replace("INV-1", "INV-2") for x in INVOICE_P1]
    docs = _docs(_pdf([INVOICE_P1, inv2]), "two.pdf")
    assert [d["pages"] for d in docs] == [[1, 1], [2, 2]]
    assert "другой номер" in docs[1]["split"]["reason"]
    assert [d["doc_number"] for d in docs] == ["INV-1", "INV-2"]


def test_excel_workbook_with_two_documents_and_header_row():
    data = _xlsx({"Invoice": [["COMMERCIAL INVOICE"], ["Invoice No", "INV-7"], [],
                              ["Item No", "Описание товара", "Контейнер", "Вес нетто, кг", "Цена за т"],
                              [1, "Каучук", "DEMU0002019", 23940, 10157], ["Итого", "", "", 23940, ""]],
                  "Packing list": [["PACKING LIST"], ["Контейнер", "Мест", "Вес брутто, кг"],
                                   ["DEMU0002019", 24, 24300]]})
    docs = _docs(data, "book.xlsx")
    assert [(d["type"], d["sheets"]) for d in docs] == [("invoice", ["Invoice"]), ("packing", ["Packing list"])]
    item = docs[0]["items"][0]["fields"]
    assert item["net_weight"]["value"] == 23940 and item["unit_price"]["value"] == 10157   # «Цена» -> цена за единицу
    assert item["container"]["source"] == {"file": "book.xlsx", "method": "table", "sheet": "Invoice", "row": 5,
                                           "col": "C"}


def test_csv_gbk_chinese_draft():
    text = "报关单草稿,,\n合同协议号,ДЕМО.9-01,\n,,\n项号,商品编号,净重,单价,币制\n1,4002391000,47880,10.157,人民币\n"
    docs = _docs(text.encode("gb18030"), "draft.csv")
    d = docs[0]
    assert d["type"] == "draft"
    it = d["items"][0]["fields"]
    assert it["hs_code"]["value"] == "4002391000" and it["currency"]["value"] == "CNY"
    assert d["fields"]["contract"]["value"] == "ДЕМО.9-01"


def test_docx_paragraphs_and_tables():
    data = docx([("b", "PACKING LIST / УПАКОВОЧНЫЙ ЛИСТ № PL-9"), "Контракт: ДЕМО.9-01",
                 [["Item No", "Контейнер", "Вес нетто, кг", "Вес брутто, кг"], ["1", "DEMU0002019", "23 940", "24 300"]]])
    d = _docs(data, "pl.docx")[0]
    assert d["type"] == "packing" and d["doc_number"] == "PL-9"
    assert d["items"][0]["fields"]["gross_weight"]["value"] == 24300


def test_legacy_doc_without_libreoffice_is_not_recognized_but_batch_survives(monkeypatch):
    monkeypatch.setattr(formats, "libreoffice_cmd", lambda: None)
    with open(os.path.join(FIX, "contract_legacy.doc"), "rb") as fh:
        doc = fh.read()
    docs = _docs(doc, "old.doc") + _docs(b"plain text", "note.txt")
    assert docs[0]["status"] == pipeline.STATUS_UNKNOWN and "LibreOffice" in docs[0]["status_reason"]
    assert docs[1]["status"] != pipeline.STATUS_ERROR


@pytest.mark.skipif(not formats.libreoffice_cmd(), reason="нет LibreOffice")
def test_legacy_doc_with_libreoffice():
    with open(os.path.join(FIX, "contract_legacy.doc"), "rb") as fh:
        d = _docs(fh.read(), "old.doc")[0]
    assert d["converted_from"] == ".doc" and d["format"] == "docx"
    assert d["type"] == "contract" and d["doc_number"] == "ДЕМО.9"


def test_zip_nested_eml_unknown_and_broken_files():
    msg = EmailMessage()
    msg["From"], msg["Subject"] = "broker@demo.example", "Документы"
    msg.set_content("Добрый день! Во вложении упаковочный лист. С уважением")
    msg.add_attachment(_pdf([PACKING]), maintype="application", subtype="pdf", filename="Упаковочный_лист.pdf")
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep/readme.txt", "С уважением, отдел ВЭД")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("Папка/письмо.eml", bytes(msg))
        zf.writestr("Папка/inner.zip", inner.getvalue())
        zf.writestr("Папка/чертёж.dwg", b"AC1027\x00\x01")
        zf.writestr("Папка/битый.pdf", b"%PDF-1.4 broken")
        zf.writestr("__MACOSX/._x", b"junk")
    docs = _docs(outer.getvalue(), "batch.zip")
    by = {d["file"]: d for d in docs}
    assert set(by) == {"batch.zip/Папка/письмо.eml", "batch.zip/Папка/письмо.eml/Упаковочный_лист.pdf",
                       "batch.zip/Папка/inner.zip/deep/readme.txt", "batch.zip/Папка/чертёж.dwg",
                       "batch.zip/Папка/битый.pdf"}
    assert by["batch.zip/Папка/письмо.eml"]["type"] == "other"
    assert by["batch.zip/Папка/письмо.eml/Упаковочный_лист.pdf"]["type"] == "packing"
    assert by["batch.zip/Папка/чертёж.dwg"]["status"] == pipeline.STATUS_UNKNOWN
    assert by["batch.zip/Папка/битый.pdf"]["status"] == pipeline.STATUS_ERROR


def test_xml_declarations_by_profile():
    import glob
    f = sorted(glob.glob(os.path.join(ROOT, "data", "demo", "ru_dt", "*.xml")))[0]
    with open(f, "rb") as fh:
        d = _docs(fh.read(), os.path.basename(f))[0]
    assert d["type"] == "ru_dt" and d["fields"]["decl_ru"]["value"] == "10000010/150926/0000001"
    assert d["items"][0]["fields"]["net_weight"]["value"] == 47880
    assert d["items"][0]["fields"]["net_weight"]["source"]["method"] == "xml"


@needs_ocr
def test_image_scan_ocr_fields_have_confidence_and_page():
    with open(os.path.join(ROOT, "data", "demo", "scans", "scan_CN_220220260000000102.jpg"), "rb") as fh:
        d = _docs(fh.read(), "scan.jpg")[0]
    assert d["type"] == "cn_dec" and d["fields"]["decl_cn"]["value"] == "220220260000000102"
    fv = d["items"][0]["fields"]["unit_price"]
    assert fv["value"] == pytest.approx(9.176) and 0 < fv["conf"] < 1
    assert fv["source"]["method"] == "ocr" and fv["source"]["page"] == 1 and fv["source"]["ocr_conf"]


def test_container_ocr_fix_only_with_valid_check_digit():
    from app.intake.extract import find_containers
    assert find_containers("DEMUO002019, DEMU0002024") == (["DEMU0002019", "DEMU0002024"],
                                                           ["DEMUO002019 → DEMU0002019"])
    assert find_containers("DEMUO002018")[0] == []          # после замены контрольная цифра не сходится


# ------------------------------------------------------------------ классификация
def test_classification_is_explained():
    c = classify.classify(classify.Features(text="PACKING LIST\nGross weight 24 300 kg\n24 pallets",
                                            top="PACKING LIST", fmt="pdf"))
    assert c.type == "packing" and c.confidence >= 0.6
    assert any("packing list" in r for r in c.reasons) and any("заголовок" in r for r in c.reasons)
    weak = classify.classify(classify.Features(text="какой-то текст без признаков", fmt="txt"))
    assert weak.type == "other" and weak.confidence < 0.6 and "не определён" in weak.reasons[-1]


def test_demo_package_classification_accuracy():
    pid = use_demo_storage()
    exp = expected()
    docs = pkg.load_meta(pid)["documents"]
    truth = {(e["file"], tuple(e["pages"] or ())): e for e in exp["documents"]}
    assert len(docs) == len(exp["documents"]) == 26
    checked = right = 0
    wrong = []
    for d in docs:
        e = truth[(d["file"], tuple(d["pages"] or ()))]
        if e["ocr"] and not formats.ocr_available():
            continue
        checked += 1
        if d["type"] == e["type"]:
            right += 1
        else:
            wrong.append((d["file"], d["type"], e["type"]))
    accuracy = right / checked
    print(f"\nТочность классификации на демо-пакете: {right}/{checked} = {accuracy:.0%}")
    assert accuracy >= 0.95, wrong


def test_demo_package_statuses_and_split():
    pid = use_demo_storage()
    docs = pkg.load_meta(pid)["documents"]
    st = {d["file"].rsplit("/", 1)[-1]: d["status"] for d in docs}
    assert st["Чертёж_упаковки.dwg"] == pipeline.STATUS_UNKNOWN
    assert st["ДТ_10000010_051126_0000117.pdf"] == pipeline.STATUS_OK
    split = [d for d in docs if d["file"].endswith("Комплект_документов_0000118.pdf")]
    assert [(d["type"], d["pages"]) for d in split] == [("invoice", [1, 2]), ("packing", [3, 3]),
                                                       ("transport", [4, 4])]
    tif = next(d for d in docs if d["file"].endswith(".tif"))
    if formats.ocr_available():
        assert tif["pages"] == [1, 2] and tif["type"] == "conformity"     # страница 2 без заголовка — продолжение


# ------------------------------------------------------------------ поля, правка типа, Excel
def test_demo_fields_items_and_sources():
    pid = use_demo_storage()
    meta = pkg.load_meta(pid)
    dt = next(d for d in meta["documents"] if d["file"].endswith("ДТ_10000010_051126_0000117.pdf"))
    doc = pkg.load_doc(pid, dt["id"])
    assert doc["fields"]["decl_ru"]["value"] == "10000010/051126/0000117"
    assert doc["fields"]["delivery_place"]["value"] == "ЗАБАЙКАЛЬСК"
    items = {it["no"]: it["fields"] for it in doc["items"]}
    assert items["1"]["net_weight"]["value"] == 47880 and items["2"]["hs_code"]["value"] == "4002201000"
    assert items["2"]["container"]["value"] == "DEMU0002030"
    src = items["2"]["hs_code"]["source"]
    assert src["page"] == 2 and src["method"] == "text" and src["file"].endswith(".pdf")


def test_override_type_reextracts_and_updates_excel():
    pid = use_demo_storage()
    meta = pkg.load_meta(pid)
    bank = next(d for d in meta["documents"] if d["type"] == "bank")
    doc = pkg.set_type(pid, bank["id"], "system_export", user="тест")
    assert doc["type"] == "system_export" and doc["type_source"] == "manual" and doc["confidence"] == 1.0
    assert doc["history"][-1]["user"] == "тест"
    s = next(x for x in pkg.load_meta(pid)["documents"] if x["id"] == bank["id"])
    assert s["type"] == "system_export" and s["type_source"] == "manual"
    wb = openpyxl.load_workbook(pkg.excel_path(pid), read_only=True)
    rows = list(wb["Документы"].iter_rows(values_only=True))
    row = next(r for r in rows if r[1] == bank["id"])
    assert row[7] == "system_export" and row[9] == "вручную"
    back = pkg.set_type(pid, bank["id"], "auto")
    assert back["type"] == "bank" and back["type_source"] == "auto"
    with pytest.raises(pkg.PackageError):
        pkg.set_type(pid, bank["id"], "no_such_type")


def test_package_excel_layout():
    pid = use_demo_storage()
    wb = openpyxl.load_workbook(pkg.excel_path(pid), read_only=True)
    names = wb.sheetnames
    assert names[0] == "Документы" and {"Требует проверки", "Источники полей", "ДТ РФ", "Инвойс"} <= set(names)
    review = list(wb["Требует проверки"].iter_rows(min_row=2, values_only=True))
    assert any(r[0] and "dwg" not in str(r[1]) for r in review) or not formats.ocr_available()


# ------------------------------------------------------------------ API
@pytest.fixture()
def api(isolated_storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import server
    monkeypatch.setattr(pkg, "PACKAGES_DIR", str(tmp_path / "packages"))
    monkeypatch.setattr(server, "UPLOADS_DIR", str(tmp_path / "uploads"))
    saved = dict(server._uploads)
    server._uploads.clear()
    yield TestClient(server.app), server
    server._uploads.clear()
    server._uploads.update(saved)


def test_api_upload_documents_override_excel_and_to_retro(api):
    client, server = api
    import glob
    xml = sorted(glob.glob(os.path.join(ROOT, "data", "demo", "ru_dt", "*.xml")))[:2]
    files = [("files", (os.path.basename(p), open(p, "rb").read(), "application/xml")) for p in xml]
    files.append(("files", ("set.pdf", _pdf([INVOICE_P1, PACKING]), "application/pdf")))
    files.append(("files", ("чертёж.dwg", b"AC1027", "application/octet-stream")))
    r = client.post("/api/intake/packages?wait=true", files=files, data={"mode": "retro", "title": "тест"})
    assert r.status_code == 200, r.text
    meta = r.json()
    pid = meta["id"]
    assert meta["mode_label"] == "ретро" and meta["status"] == "done" and len(meta["documents"]) == 5
    docs = client.get(f"/api/intake/packages/{pid}/documents").json()
    assert [d["type"] for d in docs] == ["ru_dt", "ru_dt", "invoice", "packing", "other"]
    assert client.get(f"/api/intake/packages/{pid}/documents?type=ru_dt").json()[0]["id"] == docs[0]["id"]
    one = client.get(f"/api/intake/packages/{pid}/documents/{docs[2]['id']}").json()
    assert one["text"].startswith("COMMERCIAL INVOICE") and one["pages_info"][0]["image_url"]
    assert client.get(one["pages_info"][0]["image_url"]).headers["content-type"] == "image/jpeg"
    assert client.get(one["original_url"]).status_code == 200
    r = client.put(f"/api/intake/packages/{pid}/documents/{docs[4]['id']}/type", json={"type": "reference"})
    assert r.status_code == 200 and r.json()["type"] == "reference"
    assert client.put(f"/api/intake/packages/{pid}/documents/{docs[4]['id']}/type", json={"type": "x"}).status_code == 400
    x = client.get(f"/api/intake/packages/{pid}/excel")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    st = client.get(f"/api/intake/packages/{pid}/status").json()
    assert st["status"] == "done" and st["documents"] == 5
    # «добавить в сверку»: ДТ из пакета -> слот ru_dt ретро-режима
    r = client.post(f"/api/intake/packages/{pid}/to-retro", json={})
    assert r.status_code == 200 and r.json()["added"]["ru_dt"]["documents"] == [docs[0]["id"], docs[1]["id"]]
    src = client.get("/api/sources").json()
    ru = next(s for s in src if s["slot"] == "ru_dt")
    assert ru["loaded"] and ru["rows"] == 2
    assert client.get("/api/intake/packages").json()[0]["id"] == pid
    assert client.get("/api/intake/packages/19990101-000000-dead").status_code == 404
    assert client.delete(f"/api/intake/packages/{pid}").json()["deleted"]
    assert client.get(f"/api/intake/packages/{pid}").status_code == 404


def test_api_background_processing_reports_progress(api):
    client, _ = api
    r = client.post("/api/intake/packages", files=[("files", ("a.txt", "С уважением".encode(), "text/plain"))])
    assert r.status_code == 202 and r.json()["status"] == "processing"
    pid = r.json()["id"]
    meta = pkg.wait(pid, 60)
    assert meta["status"] == "done"
    st = client.get(f"/api/intake/packages/{pid}/status").json()
    assert st["progress"]["stage"] == "готово" and st["documents"] == 1


def test_api_info():
    from fastapi.testclient import TestClient
    from app import server
    info = TestClient(server.app).get("/api/intake/info").json()
    assert {t["type"] for t in info["types"]} >= {"ru_dt", "cn_dec", "draft", "invoice", "packing", "transport",
                                                  "conformity", "contract", "system_export", "bank", "reference",
                                                  "other"}
    assert "ru_dt" in info["extractors"]


# ------------------------------------------------------------------ закрытый контур
def test_whole_pipeline_runs_without_network(monkeypatch, tmp_path):
    def no_net(*a, **k):
        raise AssertionError("попытка выхода в сеть")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(pkg, "PACKAGES_DIR", str(tmp_path))
    exp = expected()
    with open(os.path.join(ROOT, "data", "demo", "package", exp["zip"]), "rb") as fh:
        data = fh.read()
    m = pkg.create("realtime")
    m = pkg.add_files(m["id"], [(data, exp["zip"])])
    assert m["status"] == "done" and len(m["documents"]) == 26
    from app import realtime
    res = realtime.run(m["id"])
    assert res["counts"]["total"] > 0
