# -*- coding: utf-8 -*-
"""SibDoc 3: проверка ближайшей отгрузки — скан ↔ черновик ↔ система ↔ справочники ↔ БД."""
from __future__ import annotations

import os

import openpyxl
import pandas as pd
import pytest

from app import realtime
from app.intake import formats
from app.intake import package as pkg
from app.realtime import compare as cmp

from _intake_demo import use_demo_storage

RL = cmp.rules()


def _row(rows, level, field):
    hit = [r for r in rows if r["level"] == level and r["field"] == field]
    assert hit, f"нет строки {level} / {field}"
    return hit[0]


@pytest.fixture(scope="module")
def result():
    pid = use_demo_storage()
    return realtime.run(pid)


def _decl(result, doc_type, match: str | None = None):
    """Декларация нужного типа; match — подстрока имени файла (когда в пакете их несколько)."""
    items = [d for d in result["declarations"] if d["type"] == doc_type]
    if match:
        items = [d for d in items if match in str(d.get("file", ""))]
    assert items, f"нет декларации {doc_type} / {match}"
    return items[0]


def test_default_declarations_are_all_package_declarations(result):
    """По умолчанию проверяются ВСЕ декларации пакета, а не только сканы."""
    assert {(d["type"], d["format"]) for d in result["declarations"]} >= {("ru_dt", "pdf")}
    cov = result["coverage"]
    assert cov["checked"] == cov["available"] and cov["checked"] >= 2, cov
    dt = _decl(result, "ru_dt")
    assert dt["number"] == "10000010/051126/0000117"
    assert dt["draft"]["file"].endswith("Черновик_ДТ_0000117.xlsx")
    assert dt["system"]["source"] == "package" and dt["system"]["lines"] == 3


def test_ru_dt_vs_draft_system_and_references(result):
    rows = _decl(result, "ru_dt")["rows"]
    r = _row(rows, "Строка 1", "net_weight")
    assert r["status"] == "ISSUE" and r["scan"] == "47 880" and r["draft"] == "48 880" and r["system"] == "47 880"
    assert "черновик" in r["reason"] and "草稿" in r["reason_zh"]
    r = _row(rows, "Строка 2", "hs_code")
    assert r["status"] == "ISSUE" and r["reference"] == "4002191300" and "2. HS code" in r["reason"]
    r = _row(rows, "Строка 2", "unit_price")
    assert r["status"] == "ISSUE" and "1. Price" in r["reason"] and "9 761" in r["system"]
    for level, field in (("Шапка", "contract"), ("Шапка", "incoterms"), ("Шапка", "currency"),
                         ("Шапка", "container"), ("Строка 1", "hs_code"), ("Строка 1", "unit_price"),
                         ("Строка 2", "net_weight"), ("Шапка", "consignee")):
        assert _row(rows, level, field)["status"] == "OK", (level, field)
    assert "3. Incoterms" in _row(rows, "Шапка", "incoterms")["reason"]
    assert "контракт есть" in _row(rows, "Шапка", "contract")["reason"]
    c = _decl(result, "ru_dt")["counts"]
    assert c["ISSUE"] == 4 and c["REVIEW"] == 0


@pytest.mark.skipif(not formats.ocr_available(), reason="нет tesseract")
def test_cn_scan_vs_cn_draft_and_sap(result):
    d = _decl(result, "cn_dec", "扫描件")
    assert d["draft"]["file"].endswith("报关单草稿_0000117.csv")
    rows = d["rows"]
    r = _row(rows, "Строка 1", "unit_price")
    assert r["status"] == "ISSUE" and r["draft"] == "10.175" and r["scan"] == "10.157"
    assert "1. Price" in r["reason"] and "цена совпадает" in r["reason"]          # с SAP совпадает (за кг ×1000)
    assert _row(rows, "Строка 1", "net_weight")["status"] == "OK"
    de = [x for x in rows if x.get("field") == "decl_elements" and x.get("level") == "Строка 1"]
    assert not de or de[0]["status"] == "OK"        # для скана 报关单 поле может отсутствовать
    assert r["scan_conf"] is not None and 0 < r["scan_conf"] < 1


def test_result_saved_and_excel(result):
    pid = result["package_id"]
    assert realtime.last_result(pid)["counts"] == result["counts"]
    wb = openpyxl.load_workbook(realtime.excel_path(pid), read_only=True)
    assert wb.sheetnames == ["Сводка", "Сверка", "Источники"]
    rows = list(wb["Сверка"].iter_rows(values_only=True))
    assert rows[0][:2] == ("Декларация / 报关单", "Где / 位置") and len(rows) == result["counts"]["total"] + 1
    print(f"\nПроверка отгрузки на демо-пакете: {result['counts']}")


# ------------------------------------------------------------------ сравнение значений
@pytest.mark.parametrize("kind,scan,other,status", [
    ("incoterms", "C&F", "CFR QINGDAO", "OK"),
    ("contract", "ДЕМО.3001-01А", "демо.3001 - 01а", "OK"),
    ("contract", "ДЕМО.3001", "ДЕМО.3001-01А", "REVIEW"),          # совпал только основной контракт
    ("contract", "ДЕМО.3001-02А", "ДЕМО.3001-01А", "ISSUE"),
    ("hs", "4002201000", "4002201090", "REVIEW"),
    ("hs", "4002201000", "4002191300", "ISSUE"),
    ("containers", "DEMU0002019; DEMU0002024", ["DEMU0002024", "DEMU0002019"], "OK"),
    ("containers", "DEMU0002019", "DEMU0002019; DEMU0002024", "ISSUE"),
    ("model", "BIIR 232", "Bromobutyl rubber BIIR-232", "OK"),
    ("text", "DEMO IMPORT CO., LTD.", "DEMO IMPORT CO LTD", "OK"),
    ("number", "47 880", 47900, "ISSUE"),
    ("decl", "10000010 / 051126 / 0000117", "10000010/051126/0000117", "OK"),
])
def test_compare_kinds(kind, scan, other, status):
    spec = {"key": True, "tolerance_abs": 1, "fuzzy": 0.8}
    assert cmp.compare(kind, scan, other, spec, RL, "draft")[0] == status


def test_compare_missing_values():
    assert cmp.compare("number", 1, None, {"key": True}, RL, "draft") is None            # не с чем сравнить
    assert cmp.compare("number", None, 5, {"key": False}, RL, "draft") is None           # неключевое поле — не шум
    st, ru, zh = cmp.compare("number", None, 5, {"key": True}, RL, "draft")
    assert st == "REVIEW" and "не распознано" in ru and "未识别" in zh


def test_low_ocr_confidence_softens_issue():
    decl = {"id": "d001", "type": "cn_dec", "fields": {}, "items": []}
    comps = {"draft": (10, ("ISSUE", "черновик: 10 ≠ скан 9", "草稿：10 ≠ 扫描件 9"))}
    r = cmp._row(decl, "Строка 1", "1", "unit_price", "Цена", "单价", 9, {"conf": 0.4, "source": {"method": "ocr"}},
                 comps, True, RL)
    assert r["status"] == "REVIEW" and "низкая уверенность" in r["reason"] and "置信度低" in r["reason_zh"]


def test_line_matching_does_not_trust_order():
    scan = [{"_no": "1", "container": "DEMU0002019", "hs_code": "4002391000", "product_name": "Каучук БИИР"},
            {"_no": "2", "container": "DEMU0002030", "hs_code": "4002201000", "product_name": "СБС Л7322"}]
    draft = [{"_no": "1", "container": "DEMU0002030", "hs_code": "4002191300", "product_name": "СБС Л7322"},
             {"_no": "2", "container": "DEMU0002019", "hs_code": "4002391000", "product_name": "Каучук БИИР"},
             {"_no": "3", "container": "DEMU0009999", "hs_code": "2901100000", "product_name": "Этилен"}]
    pairs, lost = cmp.match_items(scan, draft, RL)
    assert [(s["_no"], d["_no"]) for s, d, _ in pairs] == [("1", "2"), ("2", "1")]
    assert [d["_no"] for d in lost] == ["3"]


# ------------------------------------------------------------------ источники: ретро-слоты и БД
def _source(df: pd.DataFrame, slot: str):
    from app.models import Source
    return Source(slot=slot, name="тест", df=df)


def test_system_from_retro_slots_and_database(monkeypatch):
    pid = use_demo_storage()
    docs = pkg.documents(pid)
    dt = next(d for d in docs if d["file"].endswith("ДТ_10000010_051126_0000117.pdf"))
    purchase = pd.DataFrame([{"Container number": "DEMU0002019", "Price": 10157, "Currency": "CNY",
                              "Main contract": "ДЕМО.3001", "Add contract": "01А", "Net weight": 23.94},
                             {"Container number": "DEMU0002024", "Price": 10157, "Currency": "CNY",
                              "Main contract": "ДЕМО.3001", "Add contract": "01А", "Net weight": 23.94}])

    class FakeDb:
        type = "database"

        def pull(self, dest):
            path = os.path.join(dest, "purchase.csv")
            purchase.assign(**{"Net weight": [23.94, 24.94]}).to_csv(path, index=False)
            return [{"slot": "purchase", "file": "purchase.csv", "path": path}]

    from app import connectors
    monkeypatch.setattr(connectors, "get_connector", lambda cfg, cid: FakeDb() if cid == "fake_db" else None)
    monkeypatch.setattr(cmp, "rules", lambda: {**RL, "database": {"connector": "fake_db", "slots": ["purchase"]}})
    res = realtime.run(pid, declarations=[dt["id"]], draft="none", system="retro",
                       retro_sources={"purchase": _source(purchase, "purchase")}, database=True)
    d = res["declarations"][0]
    assert d["system"]["source"] == "retro" and d["system"]["lines"] == 2
    assert d["database"]["source"] == "database" and d["database"]["lines"] == 2
    r = _row(d["rows"], "Строка 1", "net_weight")
    assert r["system"] == "47 880" and r["database"] == "48 880" and r["status"] == "ISSUE" and "БД" in r["reason"]
    assert d["draft"]["doc_id"] is None
    c = _row(d["rows"], "Шапка", "container")
    assert c["status"] == "ISSUE" and "DEMU0002030" in c["reason"]       # контейнера строки 2 нет в системе


def test_api_realtime_check_and_downloads(isolated_storage):
    from fastapi.testclient import TestClient
    from app import server
    pid = use_demo_storage()
    client = TestClient(server.app)
    assert client.get(f"/api/realtime/{pid}/result").status_code in (200, 404)
    r = client.post(f"/api/realtime/{pid}/check", json={"draft": "auto", "system": "package"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["ISSUE"] >= 4 and body["excel"].endswith("/excel")
    assert client.get(f"/api/realtime/{pid}/result").json()["counts"] == body["counts"]
    x = client.get(f"/api/realtime/{pid}/excel")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert client.post(f"/api/realtime/{pid}/check", json={"declarations": ["d999"]}).status_code == 404
    assert "header_fields" in client.get("/api/realtime/rules").json()
