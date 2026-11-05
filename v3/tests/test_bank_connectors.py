# -*- coding: utf-8 -*-
"""Коннекторы РФ–Китай и выписки банков (ветка 2.2, SibDoc 3): разбор выписок (1С «Клиент-банк», camt.053, MT940,
CSV банков КНР), 1С EnterpriseData, проверка «8. Оплаты», мосты export_dir (банк, 单一窗口,
ЭТРАН, 1С), статус коннекторов и эмуляторы Kingdee / Yonyou / 1С OData на настоящем HTTP
(tests/emulators.py, 127.0.0.1). Все данные вымышленные.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import shutil
import sys

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import app.journal as journal  # noqa: E402
from app import connectors  # noqa: E402
from app.connectors import ConnectorError  # noqa: E402
from app.connectors import base as cbase  # noqa: E402
from app.models import Source  # noqa: E402
from app.services import bank_statements as bs  # noqa: E402
from app.services import enterprise_data  # noqa: E402
from emulators import KingdeeEmulator, MockServer, OneCODataEmulator, YonyouEmulator  # noqa: E402

DEMO = os.path.join(ROOT, "data", "demo")
PW = "Emu-Pass-Пароль-77"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(cbase, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SIBDOC_EMU_PW", PW)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")          # эмуляторы — мимо прокси
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    # SibDoc3: все коннекторы и эмуляторы работают ВНУТРИ закрытого контура — разрешён только
    # список offline.allow_hosts из config/system.yaml (127.0.0.0/8 там есть)
    monkeypatch.setenv("SIBDOC_OFFLINE", "1")
    monkeypatch.delenv("SIBDOC_OFFLINE_ALLOW", raising=False)


@pytest.fixture(scope="module", autouse=True)
def _demo_bank():
    if not os.path.exists(os.path.join(DEMO, "bank", "kl_to_1c_demo.txt")):
        import subprocess
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "make_demo_bank.py")])


def _journal_text() -> str:
    return open(journal.JOURNAL_FILE, encoding="utf-8").read() if os.path.exists(journal.JOURNAL_FILE) else ""


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


# ============================================================================ выписки
def test_1c_client_bank_exchange_cp1251():
    df, meta = bs.parse(_read(os.path.join(DEMO, "bank", "kl_to_1c_demo.txt")))
    assert meta["format"] == "1CClientBankExchange" and meta["version"] == "1.03"
    assert meta["accounts"] == ["40702156000000000001"]
    assert len(df) == 4 and set(df["currency"]) == {"CNY"}                   # валюта — по номеру счёта (156)
    first = df.iloc[0]
    assert first["pay_direction"] == "in" and first["amount"] == 486317.16 and first["pay_date"] == "2026-09-25"
    assert first["counterparty"] == "DEMO IMPORT CO., LTD. (fictional)"
    assert "ДЕМО.1001-01А" in first["purpose"]
    out = df.iloc[3]
    assert out["pay_direction"] == "out" and out["counterparty"].startswith('ООО "Логистика')
    assert out["counterparty_inn"] == "0000000002"


def test_1c_dos_encoding_and_account_currency():
    text = ("1CClientBankExchange\nВерсияФормата=1.02\nКодировка=DOS\nРасчСчет=40702810000000000009\n"
            "СекцияДокумент=Платежное поручение\nНомер=7\nДата=01.10.2026\nСумма=100.50\n"
            "ПлательщикСчет=40702810000000000009\nДатаСписано=01.10.2026\nПолучатель=ИНН 0000000003 АО Демо\n"
            "ПолучательСчет=40702810000000000777\nНазначениеПлатежа=Оплата\nКонецДокумента\nКонецФайла\n")
    df, _ = bs.parse(text.encode("cp866"))
    assert df.iloc[0]["currency"] == "RUB" and df.iloc[0]["pay_direction"] == "out"
    assert df.iloc[0]["counterparty"] == "АО Демо"
    assert bs.account_currency("40702840000000000002") == "USD" and bs.account_currency("123") == ""


def test_camt053_entries_details_and_status():
    df, meta = bs.parse(_read(os.path.join(DEMO, "bank", "camt053_demo.xml")))
    assert meta["format"] == "ISO 20022 camt.053.001.08"
    assert list(df["amount"]) == [34870.20, 36029.66, 1000.00] and set(df["currency"]) == {"USD"}
    assert list(df["entry_status"]) == ["BOOK", "BOOK", "PDNG"]
    r = df.iloc[0]
    assert r["counterparty"] == "DEMO IMPORT CO., LTD. (fictional)" and r["counterparty_bank"] == "DEMOCNSHXXX"
    assert r["reference"] == "DEMO-E2E-0003" and "DEMO.1002-01A" in r["purpose"]
    # camt.053.001.02: Nm без Pty, Sts без Cd, сторно, пакет из двух TxDtls
    x = """<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt>
      <Acct><Id><IBAN>DE00DEMO</IBAN></Id><Ccy>EUR</Ccy></Acct>
      <Ntry><Amt Ccy="EUR">10</Amt><CdtDbtInd>CRDT</CdtDbtInd><RvslInd>true</RvslInd><Sts>BOOK</Sts>
        <BookgDt><Dt>2026-10-01</Dt></BookgDt><NtryDtls><TxDtls><RltdPties><Cdtr><Nm>X</Nm></Cdtr></RltdPties>
        </TxDtls></NtryDtls></Ntry>
      <Ntry><Amt Ccy="EUR">100</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-10-02</Dt></BookgDt>
        <NtryDtls><TxDtls><AmtDtls><TxAmt><Amt Ccy="EUR">60</Amt></TxAmt></AmtDtls>
          <RltdPties><Cdtr><Nm>A</Nm></Cdtr></RltdPties></TxDtls>
        <TxDtls><AmtDtls><TxAmt><Amt Ccy="EUR">40</Amt></TxAmt></AmtDtls><RltdPties><Cdtr><Nm>B</Nm></Cdtr></RltdPties>
          <RmtInf><Ustrd>INV 1</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry></Stmt></BkToCstmrStmt></Document>"""
    df, meta = bs.parse(x.encode())
    assert meta["accounts"] == ["DE00DEMO"]
    assert list(df["pay_direction"]) == ["out", "out", "out"] and list(df["amount"]) == [10, 60, 40]
    assert df.iloc[0]["entry_status"].startswith("RVSL") and list(df["counterparty"]) == ["X", "A", "B"]


def test_camt_rejects_entities():
    bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Document><BkToCstmrStmt/></Document>'
    with pytest.raises(bs.BankFormatError):
        bs.parse(bad)


def test_mt940_fields_and_86_structures():
    df, meta = bs.parse(_read(os.path.join(DEMO, "bank", "mt940_demo.sta")))
    assert meta["format"] == "SWIFT MT940" and meta["accounts"] == ["40702156000000000003"]
    assert list(df["amount"]) == [50000.0, 7500.0] and set(df["currency"]) == {"CNY"}
    assert list(df["counterparty"]) == ["DEMO IMPORT CO LTD", "UNKNOWN TRADING CO (fictional)"]
    assert df.iloc[0]["purpose"] == "ADVANCE PAYMENT" and df.iloc[0]["reference"] == "DEMOREF01"
    german = (":20:X\n:25:DEMOBANK/123\n:28C:1\n:60F:C261001EUR0,00\n"
              ":61:2610011001RD250,00NTRFNONREF\n:86:166?00Ueberweisung?20Rechnung?21 R-2026-7?32Demo GmbH\n"
              ":62F:C261001EUR0,00\n-\n").encode()
    df, _ = bs.parse(german)
    r = df.iloc[0]
    assert r["pay_direction"] == "in" and r["entry_status"] == "BOOK RVSL"       # RD — сторно дебета
    assert r["counterparty"] == "Demo GmbH" and "R-2026-7" in r["purpose"]


def test_cn_bank_csv_gbk_with_title_rows(cfg):
    from app.services.loader import read_table
    df, info = read_table(_read(os.path.join(DEMO, "bank_cn", "交易明细_demo.csv")), "交易明细_demo.csv", "bank", cfg)
    assert info["format"] == "csv (выписка банка)" and len(df) == 3
    assert list(df["pay_direction"]) == ["out", "out", "in"] and df.iloc[0]["amount"] == 486317.16
    assert df.iloc[0]["account"] == "6222000000000000001" and set(df["currency"]) == {"CNY"}
    assert df.iloc[0]["counterparty"].startswith("ООО Пример Экспорт") and "INV-DEMO-0001" in df.iloc[0]["purpose"]


def test_cn_bank_excel_payer_payee_columns(cfg, tmp_path):
    """Выписка вида BOC: плательщик и получатель отдельно, сумма со знаком, наш счёт в колонке."""
    from app.services.loader import read_table
    df = pd.DataFrame([
        {"交易日期": "20261005", "交易金额": "-1,000.00", "交易货币": "美元", "付款人名称": "示例进口有限公司",
         "付款人账号": "6222000000000000001", "收款人名称": "OOO PRIMER EXPORT", "收款人账号": "40702840000000000002",
         "附言": "CONTRACT DEMO.1002-01A"},
        {"交易日期": "20261006", "交易金额": "200.00", "交易货币": "美元", "付款人名称": "DEMO REFUND LTD",
         "付款人账号": "999", "收款人名称": "示例进口有限公司", "收款人账号": "6222000000000000001", "附言": "退款"}])
    p = tmp_path / "boc.xlsx"
    with pd.ExcelWriter(p) as xw:
        pd.DataFrame([["中国银行 交易明细（虚构）"]]).to_excel(xw, index=False, header=False, sheet_name="明细")
        df.to_excel(xw, index=False, sheet_name="明细", startrow=2)
    out, info = read_table(p.read_bytes(), "boc.xlsx", "bank", cfg)
    assert info["header_row"] == 3
    assert list(out["pay_direction"]) == ["out", "in"] and list(out["amount"]) == [1000.0, 200.0]
    assert list(out["counterparty"]) == ["OOO PRIMER EXPORT", "DEMO REFUND LTD"]
    assert list(out["currency"]) == ["USD", "USD"] and out.iloc[0]["account"] == "6222000000000000001"


def test_loader_warns_bank_file_in_other_slot(cfg):
    from app.services.loader import read_table
    _, info = read_table(_read(os.path.join(DEMO, "bank", "mt940_demo.sta")), "mt940_demo.sta", "cn_dec", cfg)
    assert "выписку банка" in info["warning"]


# ============================================================================ 1С EnterpriseData
def test_enterprise_data_to_slots(cfg):
    data = _read(os.path.join(DEMO, "1c", "EnterpriseData_demo.xml"))
    parts, meta = enterprise_data.parse(data)
    assert set(parts) == {"purchase", "ru_dt", "contract_register"} and meta["version"].endswith("/1.8")
    p = parts["purchase"]
    assert list(p["Main contract"]) == ["ИМП.2026-01"] * 2
    assert p.iloc[0]["Контрагент"] == "示例机械有限公司（虚构）"                       # ссылка -> справочник
    assert list(p["Net weight"]) == [2.0, 1.0] and list(p["Price"]) == [50000.0, 50000.0]   # кг -> т, цена за т
    assert set(p["ГТД (1С)"]) == {"10000010/051026/0000101"}
    r = parts["ru_dt"]
    assert list(r["hs_code"]) == ["8482100009", "8483300009"] and r.iloc[0]["consignor"].startswith("示例机械")
    c = parts["contract_register"]
    assert list(c["contract"]) == ["ИМП.2026-01", "ДЕМО.1001"]                   # номер из наименования
    assert c.iloc[0]["contract_amount"] == 150000 and set(c["currency"]) == {"CNY"}
    from app.services.loader import read_table
    df, info = read_table(data, "EnterpriseData_demo.xml", "contract_register", cfg)
    assert len(df) == 2 and "purchase" in info["note"]
    from app.services.loader import LoadError
    with pytest.raises(LoadError):
        read_table(data, "EnterpriseData_demo.xml", "cn_dec", cfg)


# ============================================================================ проверка «8. Оплаты»
def _src(cfg, slot, paths):
    from app.services.loader import read_many
    files = [(_read(p), os.path.basename(p)) for p in paths]
    df, info = read_many(files, slot, cfg)
    return Source(slot=slot, name=files[0][1], df=df, info=info)


def _run_payments(sources, cfg, **rules_over):
    from app import checks
    from app.config_loader import load_rules
    rules = load_rules("payments.yaml")
    rules.update(rules_over)
    return checks.run_check("payments", sources, rules, cfg)


@pytest.fixture()
def pay_sources(demo_sources, cfg):
    demo_sources["bank"] = _src(cfg, "bank", sorted(glob.glob(os.path.join(DEMO, "bank", "*"))))
    return demo_sources


def test_payments_demo(pay_sources, cfg, offline_rates):
    out = _run_payments(pay_sources, cfg)
    df = out.df
    assert out.counts() == {"total": 14, "OK": 6, "ISSUE": 1, "REVIEW": 7, "SKIPPED": 0}
    assert out.meta["not_booked_skipped"] == 1                                 # camt PDNG не сверяется
    pay = df[df["level"] == "платёж"].set_index("amount")
    assert pay.loc[486317.16, "status"] == "OK" and pay.loc[486317.16, "contract"] == "ДЕМО.1001-01А"
    assert pay.loc[486317.16, "vo_code"] == "10100" and pay.loc[486317.16, "invoice_ref"] == "INV-DEMO-0001"
    assert pay.loc[34870.20, "status"] == "OK"                                  # DEMO.1002-01A латиницей = ДЕМО
    third = pay.loc[502771.50]
    assert third["status"] == "REVIEW" and "третьим лицом" in third["detail"] and "第三方" in third["detail_zh"]
    assert pay.loc[36029.66, "status"] == "REVIEW" and "пересчитано" in pay.loc[36029.66, "detail"]
    assert pay.loc[7500.0, "status"] == "ISSUE" and "без контракта" in pay.loc[7500.0, "detail"]
    assert pay.loc[50000.0, "status"] == "REVIEW" and "контрагент известен" in pay.loc[50000.0, "detail"]
    assert pay.loc[12000.0, "status"] == "REVIEW"                               # расход без контракта
    con = df[df["level"] == "контракт"].set_index("contract")
    assert con.loc["ДЕМО.1001-02А", "status"] == "REVIEW" and con.loc["ДЕМО.1001-02А", "paid_share"] == 50.0
    assert con.loc["ДЕМО.1002-01А", "status"] == "OK"                           # −30 USD комиссии — в допуске
    assert "оплат по контракту" in con.loc["ДЕМО.2002-05B", "detail"]
    assert con.loc["ДЕМО.1003-01А", "status"] == "REVIEW" and con.loc["ДЕМО.1003-01А", "fx_rate"]
    assert df["status"].iloc[0] == "ISSUE"                                      # сортировка ISSUE -> OK


def test_payments_rules_switches(pay_sources, cfg, offline_rates):
    out = _run_payments(pay_sources, cfg, counterparty_mismatch="issue", contract_without_payment="issue",
                        amount_tolerance_abs=0, amount_tolerance_pct=0,
                        payment_without_contract={"in": "review", "out": "ok"})
    con = out.df[out.df["level"] == "контракт"].set_index("contract")
    assert out.df[(out.df["amount"] == 502771.50)]["status"].iloc[0] == "ISSUE"
    assert con.loc["ДЕМО.2002-05B", "status"] == "ISSUE"
    assert con.loc["ДЕМО.1002-01А", "status"] == "REVIEW"                       # без допуска: частично
    assert out.df[out.df["amount"] == 7500.0]["status"].iloc[0] == "REVIEW"
    assert out.df[out.df["amount"] == 12000.0]["status"].iloc[0] == "OK"


def test_payments_aliases_duplicates_overpay_and_no_fx(cfg, monkeypatch):
    from app.checks import _common
    monkeypatch.setattr(_common, "fx_convert", lambda *a, **k: (float("nan"), "нет курса (тест)"))
    ru = pd.DataFrame([{"decl_ru": "10000010/011026/0000001", "item_no": "1", "contract": "CX.100-01",
                        "consignee": "示例进口有限公司", "total_value": 1000.0, "currency": "CNY"}])
    bank = pd.DataFrame([
        {"pay_date": "2026-10-02", "amount": 1100.0, "currency": "CNY", "pay_direction": "in",
         "counterparty": "SHILI JINKOU YOUXIAN GONGSI", "purpose": "合同号 CX.100-01", "reference": "R1",
         "bank_format": "test", "entry_status": "BOOK"},
        {"pay_date": "2026-10-02", "amount": 1100.0, "currency": "CNY", "pay_direction": "in",
         "counterparty": "SHILI JINKOU YOUXIAN GONGSI", "purpose": "合同号 CX.100-01", "reference": "R1",
         "bank_format": "test", "entry_status": "BOOK"},
        {"pay_date": "2026-10-03", "amount": 10.0, "currency": "USD", "pay_direction": "in",
         "counterparty": "示例进口有限公司", "purpose": "СХ.100-01", "reference": "R2", "bank_format": "test",
         "entry_status": "BOOK"}])
    src = {"ru_dt": Source("ru_dt", "x", ru), "bank": Source("bank", "b", bank)}
    out = _run_payments(src, cfg, counterparty_aliases={"示例进口有限公司": ["SHILI JINKOU"]})
    pay = out.df[out.df["level"] == "платёж"]
    assert list(pay["status"]) .count("REVIEW") == 2 and list(pay["status"]).count("OK") == 1
    assert any("дубль" in d for d in pay["detail"])
    assert pay[pay["currency"] == "USD"]["contract"].iloc[0] == "CX.100-01"   # СХ кириллицей = CX
    con = out.df[out.df["level"] == "контракт"].iloc[0]
    assert con["status"] == "REVIEW" and "без курса" in con["detail"]
    # без валютного платежа и без дубля -> переплата
    out = _run_payments({"ru_dt": Source("ru_dt", "x", ru), "bank": Source("bank", "b", bank.iloc[:1])}, cfg,
                        counterparty_aliases={"示例进口有限公司": ["SHILI JINKOU"]})
    con = out.df[out.df["level"] == "контракт"].iloc[0]
    assert con["status"] == "ISSUE" and "переплата" in con["detail"] and "超额" in con["detail_zh"]
    assert out.df[out.df["level"] == "платёж"].iloc[0]["status"] == "OK"     # алиас: SHILI JINKOU = 示例进口


def test_payments_invoice_slot_and_register(cfg):
    reg = pd.DataFrame([{"contract": "ИМП.2026-01", "counterparty": "Shili Jixie (fictional)",
                         "counterparty_inn": "", "currency": "CNY", "contract_amount": 150000.0}])
    inv = pd.DataFrame([{"invoice_no": "PI-2026-015", "contract": "ИМП.2026-01", "total_value": 60000.0,
                         "currency": "CNY"}])
    bank = pd.DataFrame([{"pay_date": "2026-10-10", "amount": 60000.0, "currency": "CNY", "pay_direction": "out",
                          "counterparty": "SHILI JIXIE CO LTD", "purpose": "Оплата по инвойсу PI-2026-015",
                          "entry_status": "BOOK"}])
    src = {"contract_register": Source("contract_register", "r", reg), "invoice": Source("invoice", "i", inv),
           "bank": Source("bank", "b", bank)}
    out = _run_payments(src, cfg)
    pay = out.df[out.df["level"] == "платёж"].iloc[0]
    assert pay["contract"] == "ИМП.2026-01" and pay["matched_by"].startswith("инвойс") and pay["status"] == "OK"
    con = out.df[out.df["level"] == "контракт"].iloc[0]
    assert con["expected_from"] == "инвойсы" and con["status"] == "OK"         # инвойс важнее суммы договора
    out = _run_payments(src, cfg, expected_amount_from=["contract_register"])
    con = out.df[out.df["level"] == "контракт"].iloc[0]
    assert con["status"] == "REVIEW" and con["paid_share"] == 40.0


def test_payments_main_contract_collects_annex_payments(cfg):
    reg = pd.DataFrame([{"contract": "ДЕМО.1001", "counterparty": "DEMO IMPORT CO., LTD.", "currency": "CNY",
                         "contract_amount": 300.0}])
    ru = pd.DataFrame([{"decl_ru": "10000010/011026/0000009", "item_no": "1", "contract": "ДЕМО.1001-01А",
                        "consignee": "DEMO IMPORT CO., LTD.", "total_value": 300.0, "currency": "CNY"}])
    bank = pd.DataFrame([{"pay_date": "2026-10-02", "amount": 300.0, "currency": "CNY", "pay_direction": "in",
                          "counterparty": "DEMO IMPORT CO LTD", "purpose": "по контракту DEMO.1001-01A",
                          "entry_status": "BOOK"}])
    out = _run_payments({"contract_register": Source("contract_register", "r", reg), "ru_dt": Source("ru_dt", "d", ru),
                         "bank": Source("bank", "b", bank)}, cfg)
    con = out.df[out.df["level"] == "контракт"].set_index("contract")
    assert con.loc["ДЕМО.1001-01А", "status"] == "OK"
    assert con.loc["ДЕМО.1001", "status"] == "OK" and con.loc["ДЕМО.1001", "contract_parts"] == "ДЕМО.1001-01А"
    assert out.df[out.df["level"] == "платёж"].iloc[0]["contract"] == "ДЕМО.1001-01А"   # длинный номер важнее


def test_payments_skipped_without_bank(ref_sources, cfg):
    out = _run_payments(ref_sources, cfg)
    assert out.counts()["SKIPPED"] == 1


def test_payments_in_engine_and_reports(pay_sources, cfg, offline_rates, tmp_path):
    from openpyxl import load_workbook
    from app.services.engine import run
    from app.services.report_excel import write_excel
    from app.services.report_pdf import write_pdf
    res = run(pay_sources, cfg)
    assert res.outputs["payments"].counts()["total"] == 14
    wb = load_workbook(write_excel(res, cfg, str(tmp_path / "r.xlsx")))
    assert {"8 Оплаты", "Выписки банка"} <= set(wb.sheetnames)
    head = [c.value for c in wb["8 Оплаты"][1]]
    assert "Пояснение / 说明" in head and "Пояснение (中文) / 说明（中文）" in head
    import pymupdf
    text = "".join(pg.get_text() for pg in pymupdf.open(write_pdf(res, cfg, str(tmp_path / "r.pdf"))))
    assert "Оплаты" in text


# ============================================================================ мосты export_dir
def test_bank_bridge_detects_formats_by_content(tmp_path):
    exp = tmp_path / "exp"
    exp.mkdir()
    for f in glob.glob(os.path.join(DEMO, "bank", "*")) + glob.glob(os.path.join(DEMO, "bank_cn", "*")):
        shutil.copy(f, exp / ("выгрузка_" + os.path.basename(f)))
    (exp / "readme.txt").write_text("не выписка", encoding="utf-8")
    c = connectors.BankFxControlConnector("bank", {"export_dir": str(exp)})
    st = c.public_dict()
    assert st["status"] == "via_exports" and st["status_title"].startswith("работает через выгрузки")
    assert c.test()["ok"] is True
    got = c.pull(str(tmp_path / "out"))
    by = {os.path.basename(i["file"]): i.get("slot") for i in got}
    assert by["выгрузка_kl_to_1c_demo.txt"] == "bank" and by["выгрузка_mt940_demo.sta"] == "bank"
    assert by["выгрузка_camt053_demo.xml"] == "bank" and by["выгрузка_交易明细_demo.csv"] == "bank"
    # без export_dir — нужен доступ, и сказано, что и у кого
    st = connectors.BankFxControlConnector("bank", {}).public_dict()
    assert st["status"] == "needs_access" and "export_dir" in st["need"] and "бухгалтер" in st["need_from"]


def test_single_window_bridge(tmp_path):
    exp = tmp_path / "sw"
    exp.mkdir()
    shutil.copy(glob.glob(os.path.join(DEMO, "cn_dec_xml", "*.xml"))[0], exp / "export(1).xml")   # имя без 报关单
    shutil.copy(os.path.join(DEMO, "cn_single_window", "报关数据查询_demo.xlsx"), exp / "导出.xlsx")
    shutil.copy(os.path.join(DEMO, "wide_table_demo.xlsx"), exp / "other.xlsx")
    c = connectors.CnSingleWindowConnector("sw", {"export_dir": str(exp)})
    got = {os.path.basename(i["file"]): i for i in c.pull(str(tmp_path / "out"))}
    assert got["export(1).xml"]["slot"] == "cn_dec" and got["export(1).xml"]["detected"] == "по содержимому"
    assert got["导出.xlsx"]["slot"] == "cn_dec"
    assert got["other.xlsx"].get("slot") is None           # wide table (SAP/FineBI) — не выгрузка 报关单
    caps = {e["type"]: e for e in connectors.catalog()}
    assert caps["cn_single_window"]["capability"] == "via_exports" and "DecMessage" in caps["cn_single_window"]["exports"]


def test_etran_bridge_and_access_text(tmp_path):
    exp = tmp_path / "etran"
    exp.mkdir()
    pd.DataFrame([{"Номер накладной": "ЭД000001", "Номер вагона": "00000001", "Станция отправления": "Демо",
                   "Номер контейнера": "DEMU1012345"}]).to_excel(exp / "журнал.xlsx", index=False)
    c = connectors.EtranConnector("etran", {"export_dir": str(exp), "slot": "transport"})
    got = c.pull(str(tmp_path / "out"))
    assert got[0]["slot"] == "transport"
    st = connectors.EtranConnector("etran", {}).public_dict()
    assert st["status"] == "needs_access" and st["stage"] == "needs_access"
    for word in ("договор", "РЖД", "ЭЦП", "КриптоПро", "веб-сервис"):
        assert word in st["need"] + st["need_from"]
    assert "ТЦФТО" in st["need_from"]


def test_status_codes_for_all_types():
    caps = {e["type"]: e for e in connectors.catalog()}
    assert caps["imap"]["capability"] == "works" and caps["imap"]["verified"] == "real"
    for t in ("kingdee", "yonyou", "onec_odata"):
        assert caps[t]["capability"] == "works" and caps[t]["verified"] == "emulator"
        assert "эмулятор" in caps[t]["capability_title"]
    for t in ("etran", "cn_single_window", "bank_fx_control"):
        assert caps[t]["capability"] == "via_exports" and caps[t]["implemented"] is False
    st = connectors.ImapMailConnector("m", {}).public_dict()
    assert st["status"] == "needs_access" and "администратора почты" in st["status_title"]
    kd = connectors.KingdeeConnector("kd", {"server_url": "https://erp.test/k3cloud", "acct_id": "A", "username": "u",
                                            "password_env": "SIBDOC_EMU_PW", "form_id": "F", "field_keys": "FBillNo",
                                            "slot": "purchase"}).public_dict()
    assert kd["status"] == "works" and "на реальной системе — после выдачи доступа" in kd["status_title"]
    assert PW not in json.dumps(kd, ensure_ascii=False)


def test_api_connectors_status_and_bank_upload(isolated_storage, monkeypatch, tmp_path, offline_rates):
    from fastapi.testclient import TestClient
    import app.server as server
    monkeypatch.setattr(server, "UPLOADS_DIR", str(tmp_path / "uploads"))
    server._uploads.clear()
    c = TestClient(server.app)
    lst = c.get("/api/connectors").json()
    assert all({"status", "status_title", "need", "need_from", "verified"} <= set(x) for x in lst)
    assert {x["capability"] for x in c.get("/api/connectors/catalog").json()} == {"works", "via_exports"}
    files = [("files", (os.path.basename(f), _read(f))) for f in sorted(glob.glob(os.path.join(DEMO, "bank", "*")))]
    r = c.post("/api/upload/bank", files=files).json()
    assert r["rows"] == 9
    src = {s["slot"]: s for s in c.get("/api/sources").json()}
    assert src["bank"]["loaded"] and src["contract_register"]["loaded"] is False
    run = c.post("/api/run").json()
    assert "payments" in run["checks"] and run["checks"]["payments"]["counts"]["total"] > 0


# ============================================================================ эмулятор Kingdee
ROWS = [{"FBillNo": f"CGDD{i:06d}", "FDate": f"2026-10-0{i}", "FQty": i * 10} for i in range(1, 6)]


def _kd(url, **over):
    cfg = {"server_url": url + "/k3cloud", "acct_id": "DEMO-ACCT", "username": "api", "password_env": "SIBDOC_EMU_PW",
           "form_id": "PUR_PurchaseOrder", "field_keys": "FBillNo,FDate,FQty", "page_size": 2, "slot": "purchase"}
    cfg.update(over)
    return connectors.KingdeeConnector("kd", cfg)


def test_kingdee_emulator_paging_and_cookie_session(tmp_path):
    with MockServer(KingdeeEmulator(ROWS, password=PW)) as srv:
        got = _kd(srv.url).pull(str(tmp_path))
        assert got[0]["rows"] == 5
        rows = list(csv.DictReader(open(got[0]["path"], encoding="utf-8-sig")))
        assert rows[0] == {"FBillNo": "CGDD000001", "FDate": "2026-10-01", "FQty": "10"} and rows[-1]["FQty"] == "50"
        queries = [r for r in srv.calls if r.path.endswith("ExecuteBillQuery.common.kdsvc")]
        assert [json.loads(q.body)["data"]["StartRow"] for q in queries] == [0, 2, 4]
        assert "kdservice-sessionid=" in queries[0].headers.get("Cookie", "")
        assert _kd(srv.url).test()["ok"] is True
    assert PW not in _journal_text()


def test_kingdee_emulator_auth_failure_and_app_secret(tmp_path, monkeypatch):
    with MockServer(KingdeeEmulator(ROWS, password="other")) as srv:
        res = _kd(srv.url).test()
        assert res["ok"] is False and "вход не выполнен" in res["detail"] and "用户名或密码错误" in res["detail"]
        monkeypatch.setenv("SIBDOC_EMU_APPSECRET", "app-secret-1")
        srv.app.app_secret = "app-secret-1"
        c = _kd(srv.url, auth_mode="app_secret", app_id="APP1", app_secret_env="SIBDOC_EMU_APPSECRET",
                password_env=None)
        assert c.missing() == []
        assert c.pull(str(tmp_path))[0]["rows"] == 5
        assert json.loads(srv.calls[-4].body)["appSecret"] == "app-secret-1"
        assert _kd(srv.url, auth_mode="app_secret", password_env=None).missing() == ["app_id", "app_secret"]
    assert "app-secret-1" not in _journal_text()


def test_kingdee_emulator_session_expiry_relogin(tmp_path):
    emu = KingdeeEmulator(ROWS, password=PW, expire_after=1)
    with MockServer(emu) as srv:
        c = _kd(srv.url)
        got = c.pull(str(tmp_path))
        assert got[0]["rows"] == 5 and emu.logins == 3          # сессия «живёт» один запрос: вход + 2 повторных
        assert any("повторный вход" in n for n in c.last_notes)


def test_kingdee_emulator_error_response_and_legacy_body(tmp_path):
    with MockServer(KingdeeEmulator(ROWS, password=PW)) as srv:
        with pytest.raises(ConnectorError) as ei:
            _kd(srv.url, field_keys="FBillNo,FNope").pull(str(tmp_path))
        assert "字段FNope不存在" in str(ei.value) and "поле или значение не существует" in str(ei.value)
        got = _kd(srv.url, body_style="parameters").pull(str(tmp_path / "p"))
        assert got[0]["rows"] == 5 and "parameters" in json.loads(srv.calls[-1].body)


# ============================================================================ эмулятор Yonyou
RECS = [{"code": f"PO{i:03d}", "vendor": "示例机械有限公司（虚构）", "amount": i * 100} for i in range(1, 8)]


def _yy(url, **over):
    cfg = {"auth_url": url + "/iuap-api-auth", "gateway_url": url + "/iuap-api-gateway", "app_key": "AK",
           "app_secret_env": "SIBDOC_EMU_PW", "api_path": "/yonbip/scm/purchaseorder/list", "page_size": 3,
           "slot": "purchase"}
    cfg.update(over)
    return connectors.YonyouConnector("yy", {k: v for k, v in cfg.items() if v is not None})


def test_yonyou_emulator_discovery_signature_paging(tmp_path):
    emu = YonyouEmulator(RECS, app_secret=PW)
    with MockServer(emu) as srv:
        emu.base = srv.url
        c = _yy(srv.url, auth_url=None, gateway_url=None, tenant_id="T1",
                discovery_url=srv.url + "/open-auth/dataCenter/getGatewayAddress")
        assert c.missing() == []
        got = c.pull(str(tmp_path))
        assert got[0]["rows"] == 7
        api = [r for r in srv.calls if r.path.startswith("/iuap-api-gateway/")]
        assert [json.loads(r.body)["pageIndex"] for r in api] == [1, 2, 3]      # стоп по pageCount
        tok = [r for r in srv.calls if r.path.endswith("getAccessToken")][0]
        assert tok.q("signature") and tok.q("appKey") == "AK"
        assert c.test()["ok"] is True
    text = _journal_text()
    assert PW not in text and "YT-" not in text


def test_yonyou_emulator_auth_failure_and_error(tmp_path):
    emu = YonyouEmulator(RECS, app_secret="other")
    with MockServer(emu) as srv:
        res = _yy(srv.url).test()
        assert res["ok"] is False and "10000" in res["detail"] and "签名不正确" in res["detail"]
        emu.app_secret = PW
        with pytest.raises(ConnectorError) as ei:
            _yy(srv.url, body={"bad": True}).pull(str(tmp_path))
        assert "999" in str(ei.value) and "参数错误" in str(ei.value)
        with pytest.raises(ConnectorError):
            _yy(srv.url, api_path="/yonbip/none").pull(str(tmp_path))


@pytest.mark.parametrize("style", ["payload", "http401"])
def test_yonyou_emulator_token_refresh(tmp_path, style):
    emu = YonyouEmulator(RECS, app_secret=PW, expired_style=style)
    with MockServer(emu) as srv:
        c = _yy(srv.url)
        c._session()                                       # прогрев: первый токен
        emu.revoke_next = True                             # следующий запрос — «токен истёк»
        s = c._session()
        emu.revoke_next = True
        recs = c._fetch(s)
        assert len(recs) == 7 and any("токен обновлён" in n for n in c.last_notes)
        assert len([r for r in srv.calls if r.path.endswith("getAccessToken")]) == 3


# ============================================================================ эмулятор 1С OData
DOCS = [{"Number": f"0000-{i:06d}", "Date": "2026-10-01T00:00:00", "Posted": i != 3, "СуммаДокумента": i * 1000}
        for i in range(1, 6)]
ENTITY = "Document_ПоступлениеТоваровУслуг"


def _onec(url, **over):
    cfg = {"base_url": url + "/trade", "entity": ENTITY, "username": "sibdoc", "password_env": "SIBDOC_EMU_PW",
           "filter": "Posted eq true", "page_size": 2, "slot": "purchase"}
    cfg.update(over)
    return connectors.OneCODataConnector("onec", {k: v for k, v in cfg.items() if v is not None})


def test_onec_emulator_paging_filter_and_test(tmp_path):
    with MockServer(OneCODataEmulator({ENTITY: DOCS, "Catalog_Контрагенты": []}, password=PW)) as srv:
        c = _onec(srv.url)
        res = c.test()
        assert res["ok"] is True and "есть в составе" in res["detail"]
        got = c.pull(str(tmp_path))
        assert got[0]["rows"] == 4                                               # проведённые
        rows = list(csv.DictReader(open(got[0]["path"], encoding="utf-8-sig")))
        assert [r["Number"] for r in rows] == ["0000-000001", "0000-000002", "0000-000004", "0000-000005"]
        pages = [r for r in srv.calls if r.path.endswith(ENTITY)]
        assert [r.q("$skip") for r in pages] == ["0", "2", "4"]


def test_onec_emulator_auth_and_odata_errors(tmp_path):
    with MockServer(OneCODataEmulator({ENTITY: DOCS}, password="other")) as srv:
        res = _onec(srv.url).test()
        assert res["ok"] is False and "HTTP 401" in res["detail"]
        srv.app.password = PW
        res = _onec(srv.url, entity="Document_Нет").test()
        assert res["ok"] is False and "НЕ включён в состав OData" in res["detail"]
        with pytest.raises(ConnectorError) as ei:
            _onec(srv.url, entity="Document_Нет").pull(str(tmp_path))
        assert "1С: Не найден ресурс: Document_Нет" in str(ei.value)
        with pytest.raises(ConnectorError) as ei:
            _onec(srv.url, filter="Posted eq 1/0").pull(str(tmp_path))
        assert "Ошибка в выражении" in str(ei.value)
    assert PW not in _journal_text()


def test_onec_export_bridge_enterprise_data(tmp_path):
    exp = tmp_path / "1c"
    exp.mkdir()
    shutil.copy(os.path.join(DEMO, "1c", "EnterpriseData_demo.xml"), exp / "Message_1_2.xml")
    shutil.copy(os.path.join(DEMO, "bank", "kl_to_1c_demo.txt"), exp / "kl_to_1c.txt")
    c = connectors.OneCODataConnector("onec", {"export_dir": str(exp)})
    assert c.public_dict()["status"] == "via_exports"
    got = c.pull(str(tmp_path / "out"))
    slots = sorted(i["slot"] for i in got)
    assert slots == ["bank", "contract_register", "purchase", "ru_dt"]


# ============================================================================ SibDoc3: закрытый контур
def _no_network(monkeypatch):
    """Любая попытка соединения — ошибка теста (отказ контура должен быть ДО соединения)."""
    import requests

    def boom(*a, **k):
        raise AssertionError("попытка соединения в закрытом контуре")
    monkeypatch.setattr(requests.Session, "send", boom)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", boom)


@pytest.mark.parametrize("make", [
    lambda: _kd("https://k3cloud.example.cn"),
    lambda: _yy("https://c2.yonyoucloud.com"),
    lambda: _yy("", auth_url=None, gateway_url=None, tenant_id="T1"),          # сервис дата-центров Yonyou
    lambda: _onec("https://1c.example.ru"),
])
def test_contour_blocks_external_connectors_before_connect(make, tmp_path, monkeypatch):
    _no_network(monkeypatch)
    c = make()
    res = c.test()
    assert res["ok"] is False and "Закрытый контур" in res["detail"]
    with pytest.raises(ConnectorError) as ei:
        c.pull(str(tmp_path))
    assert "Закрытый контур" in str(ei.value)


def test_contour_blocks_address_from_api_response(tmp_path):
    """Yonyou: адрес шлюза из ответа сервиса дата-центров (вне контура) отклоняется на самом запросе."""
    emu = YonyouEmulator(RECS, app_secret=PW)
    with MockServer(emu) as srv:
        emu.base = "https://c9.yonyoucloud.com"
        c = _yy(srv.url, auth_url=None, gateway_url=None, tenant_id="T1",
                discovery_url=srv.url + "/open-auth/dataCenter/getGatewayAddress")
        with pytest.raises(ConnectorError) as ei:
            c.pull(str(tmp_path))
        assert "Закрытый контур" in str(ei.value) and "c9.yonyoucloud.com" in str(ei.value)
        assert all(not r.path.startswith("/iuap-api") for r in srv.calls)     # до шлюза дело не дошло


def test_contour_allows_local_server_and_export_bridges(tmp_path, monkeypatch):
    """Эмулятор на 127.0.0.1 — внутри контура; мосты export_dir в сеть не ходят даже с внешним адресом."""
    with MockServer(KingdeeEmulator(ROWS, password=PW)) as srv:
        assert _kd(srv.url).test()["ok"] is True
    _no_network(monkeypatch)
    exp = tmp_path / "cn"
    exp.mkdir()
    for f in glob.glob(os.path.join(DEMO, "cn_single_window", "*")):
        shutil.copy(f, exp)
    c = connectors.CnSingleWindowConnector("sw", {"export_dir": str(exp), "service_url": "https://swapp.singlewindow.cn"})
    assert c.test()["ok"] is True
    assert c.pull(str(tmp_path / "out"))
    with pytest.raises(ConnectorError) as ei:
        connectors.CnSingleWindowConnector("sw2", {"service_url": "https://swapp.singlewindow.cn"}).pull(str(tmp_path))
    assert "Закрытый контур" in str(ei.value) or "нужен доступ" in str(ei.value).lower()
