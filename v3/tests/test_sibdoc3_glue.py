# -*- coding: utf-8 -*-
"""SibDoc 3: связка веток — пакетная загрузка читает документы настоящими разборщиками слотов
(пакет ВЭД — app/services/package_docs.py, выписки банка — bank_statements, реестр договоров и
1С EnterpriseData), «В сверку» раскладывает их по слотам ретро-режима, и проверки по пакету,
загруженному одним действием, дают те же числа, что и файлы, загруженные в слоты по одному
(verify.sh). Все данные вымышленные.
"""
from __future__ import annotations

import glob
import io
import os

import pandas as pd
import pytest

from app.intake import extract as ex
from app.intake import package as pkg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEMO = os.path.join(ROOT, "data", "demo")


@pytest.fixture(scope="module", autouse=True)
def _demo_files():
    if not os.path.exists(os.path.join(DEMO, "bank", "kl_to_1c_demo.txt")):
        import subprocess
        import sys
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "make_demo_bank.py")])


@pytest.fixture()
def api(isolated_storage, monkeypatch, tmp_path, offline_rates):
    from fastapi.testclient import TestClient
    from app import server
    monkeypatch.setenv("SIBDOC_OFFLINE", "1")                      # всё — внутри закрытого контура
    monkeypatch.setattr(pkg, "PACKAGES_DIR", str(tmp_path / "packages"))
    monkeypatch.setattr(server, "UPLOADS_DIR", str(tmp_path / "uploads"))
    saved = dict(server._uploads)
    server._uploads.clear()
    yield TestClient(server.app), server
    server._uploads.clear()
    server._uploads.update(saved)


def _files(paths):
    return [("files", (os.path.basename(p), open(p, "rb").read())) for p in paths]


def _package(client, paths, mode="retro"):
    r = client.post("/api/intake/packages?wait=true", files=_files(paths), data={"mode": mode})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    return pid, client.get(f"/api/intake/packages/{pid}/documents").json()


def _full(client, pid, doc_id):
    return client.get(f"/api/intake/packages/{pid}/documents/{doc_id}").json()


def test_extractors_registered_for_new_types():
    reg = ex.extractors()
    for t in ("invoice", "packing", "transport", "conformity"):
        assert reg[t][0] == "ВЭД-пакет (package_docs)"
    assert reg["bank"][0].startswith("выписка банка") and reg["contract"][0].startswith("реестр договоров")
    assert "1С EnterpriseData" in reg["system_export"]


def test_bank_statements_in_bulk_upload(api):
    client, _ = api
    paths = sorted(glob.glob(os.path.join(DEMO, "bank", "*"))) + sorted(glob.glob(os.path.join(DEMO, "bank_cn", "*")))
    pid, docs = _package(client, paths)
    by = {d["name"]: d for d in docs}
    assert {d["type"] for d in docs} == {"bank"}, [(d["name"], d["type"]) for d in docs]
    want = {"kl_to_1c_demo.txt": (4, "1CClientBankExchange"), "camt053_demo.xml": (3, "camt.053"),
            "mt940_demo.sta": (2, "MT940"), "交易明细_demo.csv": (3, "csv")}
    for name, (n, fmt) in want.items():
        full = _full(client, pid, by[name]["id"])
        assert len(full["items"]) == n and full["extractor"].startswith("Выписка банка") and fmt in full["extractor"], name
        f = full["items"][0]["fields"]
        assert {"pay_date", "amount"} <= set(f) and f["amount"]["source"]["method"] == "bank_statement"
    kl = _full(client, pid, by["kl_to_1c_demo.txt"]["id"])
    assert kl["fields"]["account"]["value"] == "40702156000000000001" and kl["fields"]["currency"]["value"] == "CNY"
    # «В сверку»: исходные файлы выписок — в слот «Банк: выписки» (формат определяет сам слот)
    r = client.post(f"/api/intake/packages/{pid}/to-retro", json={}).json()
    assert r["added"]["bank"]["rows"] == 12 and not r["skipped"]


def test_ved_documents_read_like_slots_with_cell_sources(api):
    client, _ = api
    paths = [os.path.join(DEMO, "package", "invoice", "invoice_INV-DEMO-2601.xlsx"),
             os.path.join(DEMO, "package", "invoice", "invoice_INV-DEMO-2603.pdf"),
             os.path.join(DEMO, "package", "invoice", "invoice_INV-DEMO-2606.csv")]
    pid, docs = _package(client, paths)
    assert [d["type"] for d in docs] == ["invoice"] * 3
    xl, pdf, csv_ = (_full(client, pid, d["id"]) for d in docs)
    assert xl["extractor"].startswith("ВЭД-пакет: инвойс") and xl["fields"]["invoice_no"]["value"] == "INV-DEMO-2601"
    src = xl["fields"]["invoice_no"]["source"]
    assert src["method"] == "table" and src.get("row") and src.get("col")          # ячейка над таблицей
    item = xl["items"][0]["fields"]
    assert item["total_value"]["value"] == 486317.16 and item["total_value"]["source"].get("col")
    assert pdf["fields"]["invoice_no"]["value"] == "INV-DEMO-2603" and pdf["fields"]["currency"]["value"] == "USD"
    assert "шаблон config/scan_fields.yaml → package" in pdf["fields"]["invoice_total"]["how"]
    # CSV с «;» и десятичной запятой: значения — как в слоте, а не как при делении по запятой
    assert csv_["fields"]["buyer"]["value"] == "DEMO IMPORT CO., LTD. (fictional)"
    assert csv_["fields"]["invoice_total"]["value"] == 245552.58


def test_contract_register_and_enterprise_data(api):
    client, server = api
    reg = io.BytesIO()
    pd.DataFrame([{"Договор": "ДЕМО.7001-01А", "Дата договора": "01.08.2026", "Контрагент": "ООО «Пример» (вымышл.)",
                   "Валюта": "CNY", "Сумма договора": 100000},
                  {"Договор": "ДЕМО.7002-01А", "Дата договора": "02.08.2026", "Контрагент": "DEMO CO (fictional)",
                   "Валюта": "USD", "Сумма договора": 5000}]).to_excel(reg, index=False)
    p = os.path.join(server.UPLOADS_DIR, "..", "реестр договоров.xlsx")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(reg.getvalue())
    ed = os.path.join(DEMO, "1c", "EnterpriseData_demo.xml")
    pid, docs = _package(client, [p, ed])
    by = {d["name"]: d for d in docs}
    c = by["реестр договоров.xlsx"]
    if c["type"] != "contract":                                     # тип правится вручную, поля — заново
        client.put(f"/api/intake/packages/{pid}/documents/{c['id']}/type", json={"type": "contract"})
    full = _full(client, pid, c["id"])
    assert full["extractor"].startswith("Реестр договоров") and len(full["items"]) == 2
    assert {it["fields"]["contract"]["value"] for it in full["items"]} == {"ДЕМО.7001-01А", "ДЕМО.7002-01А"}
    e = _full(client, pid, by["EnterpriseData_demo.xml"]["id"])
    assert e["type"] == "system_export" and e["extractor"].startswith("1С EnterpriseData") and e["items"]
    r = client.post(f"/api/intake/packages/{pid}/to-retro", json={}).json()
    assert {"contract_register", "purchase", "ru_dt"} <= set(r["added"]), r
    assert r["added"]["contract_register"]["documents"].count(c["id"]) == 1
    src = {s["slot"]: s for s in client.get("/api/sources").json()}
    assert src["contract_register"]["loaded"] and src["contract_register"]["rows"] >= 3


def test_bulk_package_to_retro_gives_same_numbers_as_slots(api):
    """Пакет из 20 документов одним действием -> «В сверку» -> те же числа, что verify.sh."""
    client, _ = api
    for slot, name in (("wide_table", "wide_table_demo.xlsx"), ("purchase", "purchase_demo.xlsx"),
                       ("sap_vs_cd", "sap_vs_cd_demo.csv")):
        assert client.post(f"/api/upload/{slot}", files=_files([os.path.join(DEMO, name)])).status_code == 200
    paths = sorted(glob.glob(os.path.join(DEMO, "ru_dt", "*.xml"))) + [os.path.join(DEMO, "cn_dec_demo.xlsx")]
    for kind in ("invoice", "packing", "transport", "conformity"):
        paths += sorted(glob.glob(os.path.join(DEMO, "package", kind, "*")))
    paths += sorted(glob.glob(os.path.join(DEMO, "bank", "*")))
    pid, docs = _package(client, paths)
    assert len(docs) == 20
    r = client.post(f"/api/intake/packages/{pid}/to-retro", json={}).json()
    assert not r["skipped"], r["skipped"]
    assert {k: len(v["documents"]) for k, v in r["added"].items()} == {
        "ru_dt": 6, "cn_dec": 1, "invoice": 6, "packing": 1, "transport": 2, "conformity": 1, "bank": 3}
    run = client.post("/api/run").json()["checks"]
    c7, c8 = run["docs_package"]["counts"], run["payments"]["counts"]
    assert (c7["total"], c7["OK"], c7["ISSUE"], c7["REVIEW"]) == (37, 28, 7, 2)
    assert c8["total"] == 14 and c8["ISSUE"] >= 1
    assert run["cross_border"]["counts"]["ISSUE"] == 4
