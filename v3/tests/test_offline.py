# -*- coding: utf-8 -*-
"""SibDoc3: закрытый контур — проверка исходящих обращений, курсы из файлов,
комплект без интернета, обновление с носителя, интерфейс без внешних ресурсов.

Сеть не используется: любой реальный запрос в этих тестах — ошибка (подмены ниже
падают, если до них дошло дело).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types
import zipfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app.journal as journal  # noqa: E402
from app import netguard, rates  # noqa: E402
from app.rates import files as rfiles  # noqa: E402
from app.rates import providers, storage  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "rates")
CFG_ON = {"offline": {"enabled": True, "allow_hosts": ["localhost", "127.0.0.0/8", "10.0.0.0/8",
                                                       "1c-server", "*.corp.local"]}}


@pytest.fixture()
def contour(monkeypatch, tmp_path):
    """Закрытый контур включён; разрешены локальная сеть 10.0.0.0/8, 1c-server, *.corp.local."""
    monkeypatch.setenv("SIBDOC_OFFLINE", "1")
    monkeypatch.setattr(netguard, "_load_cfg", lambda: CFG_ON)
    monkeypatch.setattr(journal, "JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    return tmp_path


@pytest.fixture()
def rates_env(contour, monkeypatch):
    monkeypatch.setattr(storage, "RATES_DIR", str(contour / "rates"))
    monkeypatch.setattr(storage, "CONFIG_FILE", str(contour / "rates.yaml"))
    monkeypatch.setattr(providers, "today", lambda: dt.date(2026, 10, 22))
    storage._MEMO.clear()
    rates._reset_memory()
    yield contour
    storage._MEMO.clear()
    rates._reset_memory()


@pytest.fixture()
def no_network(monkeypatch):
    """Любая попытка соединения — провал теста."""
    import socket

    import requests
    calls = []

    def boom(*a, **kw):
        calls.append(a)
        raise AssertionError(f"сетевое обращение не должно было начаться: {a[:2]}")
    monkeypatch.setattr(requests.sessions.Session, "send", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    return calls


def journal_records() -> list[dict]:
    if not os.path.exists(journal.JOURNAL_FILE):
        return []
    with open(journal.JOURNAL_FILE, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


# ============================================================================ проверка адресов
def test_project_config_contour_on_by_default(monkeypatch):
    monkeypatch.delenv("SIBDOC_OFFLINE", raising=False)
    from app.config_loader import load_config
    cfg = load_config()
    st = netguard.settings(cfg)
    assert st["enabled"] is True and st["source"] == "config"
    assert netguard.status(cfg)["label"] == "Закрытый контур: интернет не используется"
    assert netguard.blocked_reason("https://www.cbr.ru/scripts/XML_daily.asp", cfg=cfg)
    assert not netguard.blocked_reason("http://127.0.0.1:8000/api/health", cfg=cfg)
    assert not netguard.blocked_reason("192.168.1.20:1433", cfg=cfg)
    # раздела нет вовсе — контур включён и разрешён только сам компьютер
    assert netguard.settings({})["enabled"] is True
    assert netguard.blocked_reason("10.1.1.1", cfg={})


@pytest.mark.parametrize("target", [
    "https://www.cbr.ru/scripts/XML_daily.asp", "https://www.chinamoney.com.cn/r/cms/x.json",
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send", "https://api.telegram.org/botX/sendMessage",
    "imap.yandex.ru", "smtp.gmail.com:465", "8.8.8.8", "[2001:4860::8888]:443",
    "mssql+pyodbc://svc:pw@sql.example.com:1433/DB?driver=x", "postgresql:///db?host=db.example.com",
    "https://1c-server.evil.com/odata", "https://corp.local.evil.com/x",
])
def test_guard_blocks_outside_hosts(contour, target):
    t0 = time.monotonic()
    with pytest.raises(netguard.OfflineBlocked, match="Закрытый контур") as ei:
        netguard.check(target, "тест")
    assert time.monotonic() - t0 < 0.5                     # без DNS и таймаутов
    assert "offline.allow_hosts" in str(ei.value) and "(тест)" in str(ei.value)
    assert "pw" not in str(ei.value)                       # пароль из строки БД не показывается


@pytest.mark.parametrize("target", [
    "http://localhost:8000", "http://127.0.0.1/x", "10.20.30.40:5432", "https://10.0.0.5/sap/opu/odata",
    "https://1c-server/trade/odata", "sql01.CORP.local", "https://erp.corp.local:8443/k3cloud",
    "sqlite:///storage/x.db", "postgresql:///db?host=/var/run/postgresql", "",
])
def test_guard_allows_lan_hosts(contour, target):
    netguard.check(target, "тест")


def test_guard_env_switch_and_extra_hosts(monkeypatch):
    monkeypatch.setattr(netguard, "_load_cfg", lambda: CFG_ON)
    monkeypatch.setenv("SIBDOC_OFFLINE", "0")
    assert netguard.status()["offline"] is False
    assert netguard.status()["label"] == "Есть доступ в интернет"
    netguard.check("https://www.cbr.ru/")                                      # контур выключен
    monkeypatch.setenv("SIBDOC_OFFLINE", "1")
    monkeypatch.setenv("SIBDOC_OFFLINE_ALLOW", "sap01, 172.20.0.0/16")
    netguard.check("https://sap01:44300/x")
    netguard.check("172.20.5.5")
    with pytest.raises(netguard.OfflineBlocked):
        netguard.check("172.21.0.1")
    assert netguard.settings()["source"] == "env"


def test_guarded_requests_blocks_redirects_and_module_calls(contour, no_network):
    import requests
    g = netguard.guarded_requests(requests, RuntimeError, "проба")
    with pytest.raises(RuntimeError, match="Закрытый контур: обращение к example.com \\(проба\\)"):
        g.post("https://example.com/hook", json={})
    with pytest.raises(RuntimeError):
        g.Session().get("https://example.com/")
    # редирект с сервера в контуре наружу: проверка в send()
    req = requests.Request("GET", "https://evil.example/").prepare()
    with pytest.raises(RuntimeError, match="evil.example"):
        g.Session().send(req)
    assert no_network == []
    assert g.Timeout is requests.Timeout                     # остальное — как у requests


# ============================================================================ коннекторы
def test_connector_outside_contour_blocked_without_network(contour, no_network, tmp_path):
    from app import connectors
    c = connectors.RestConnector("api", {"url": "https://api.broker.example/v1", "slot": "purchase"})
    res = c.test()
    assert res["ok"] is False and res["detail"].startswith("Закрытый контур: обращение к api.broker.example")
    with pytest.raises(connectors.ConnectorError, match="Закрытый контур"):
        c.pull(str(tmp_path))
    sap = connectors.SapODataConnector("sap", {"base_url": "https://sap.example.ru/sap/opu/odata/x",
                                               "entity": "A_PurchaseOrder", "username": "u",
                                               "password": "p"})
    assert "sap.example.ru" in sap.test()["detail"]
    kinds = [r["kind"] for r in journal_records()]
    assert "connector_test" in kinds and "connector_error" in kinds
    assert no_network == []


def test_imap_sftp_db_blocked_before_connect(contour, monkeypatch, tmp_path):
    import imaplib

    from app import connectors

    def boom(*a, **kw):
        raise AssertionError("IMAP не должен подключаться")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", boom)
    monkeypatch.setattr(imaplib, "IMAP4", boom)
    m = connectors.ImapMailConnector("mail", {"host": "imap.mail.ru", "username": "u", "password": "p"})
    with pytest.raises(connectors.ConnectorError, match="imap.mail.ru"):
        m._connect()
    assert "Закрытый контур" in m.test()["detail"]
    monkeypatch.setenv("SIBDOC_TEST_DB", "mssql+pyodbc://svc:secret@db.example.com/DB")
    db = connectors.DatabaseConnector("db", {"url_env": "SIBDOC_TEST_DB", "queries": {"purchase": "SELECT 1"}})
    res = db.test()
    assert "db.example.com" in res["detail"] and "secret" not in res["detail"]
    sftp = connectors.SftpConnector("s", {"host": "sftp.example.ru", "username": "u", "password": "p",
                                          "remote_dir": "/in"})
    assert "Закрытый контур" in sftp.test()["detail"]
    # папка выгрузок «задела» (Единое окно КНР) сеть не использует — контур не мешает
    folder = tmp_path / "export"
    folder.mkdir()
    stub = connectors.CnSingleWindowConnector("sw", {"export_dir": str(folder),
                                                     "api_url": "https://swapp.singlewindow.cn"})
    assert stub._offline_gate() is None


def test_rest_inside_contour_works_but_next_link_outside_is_blocked(contour, monkeypatch, tmp_path):
    import requests

    from app import connectors
    calls = []

    class R:
        status_code = 200
        text = ""

        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    def fake(self, method, url, **kw):
        calls.append(url)
        return R({"items": [{"a": 1}], "next": "https://evil.example/v1?page=2"})
    monkeypatch.setattr(requests.sessions.Session, "request", fake)
    c = connectors.RestConnector("lan", {"url": "http://10.20.0.5/v1", "records_path": "items",
                                         "next_link_path": "next", "slot": "purchase"})
    with pytest.raises(connectors.ConnectorError, match="evil.example"):
        c.pull(str(tmp_path))
    assert calls == ["http://10.20.0.5/v1"]                  # наружу запрос не ушёл


# ============================================================================ уведомления
def test_notify_channels_blocked(contour, no_network, monkeypatch):
    import smtplib

    from app import notify
    monkeypatch.setenv("SIBDOC_TEST_WECOM", "k-123")
    monkeypatch.setenv("SIBDOC_TEST_TG", "1:tok")

    def boom(*a, **kw):
        raise AssertionError("SMTP не должен подключаться")
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
    monkeypatch.setattr(smtplib, "SMTP", boom)
    cfg = {"notifications": {
        "wc": {"type": "wecom", "enabled": True, "key_env": "SIBDOC_TEST_WECOM"},
        "tg": {"type": "telegram", "enabled": True, "token_env": "SIBDOC_TEST_TG", "chat_id": "1"},
        "mail": {"type": "email", "enabled": True, "host": "smtp.yandex.ru", "from": "a@b.ru",
                 "to": ["c@d.ru"]}}}
    out = notify.send(cfg, "run_done", "тест")
    assert len(out) == 3 and not any(r["ok"] for r in out)
    for r in out:
        assert r["detail"].startswith("Закрытый контур: обращение к"), r
    assert "qyapi.weixin.qq.com" in out[0]["detail"] and "api.telegram.org" in out[1]["detail"]
    assert "k-123" not in json.dumps(journal_records(), ensure_ascii=False)
    assert no_network == []


def test_notify_lan_bot_api_allowed(contour, monkeypatch):
    import requests

    from app import notify
    monkeypatch.setenv("SIBDOC_TEST_TG", "1:tok")
    seen = []

    class R:
        status_code = 200

        def json(self):
            return {"ok": True}

    def fake(self, method, url, **kw):
        seen.append(url)
        return R()
    monkeypatch.setattr(requests.sessions.Session, "request", fake)
    ch = {"type": "telegram", "enabled": True, "token_env": "SIBDOC_TEST_TG", "chat_id": "1",
          "api_url": "http://10.0.0.7:8081"}
    res = notify.send({"notifications": {"tg": ch}}, "run_done", "тест")
    assert res[0]["ok"] is True and seen[0].startswith("http://10.0.0.7:8081/bot")


# ============================================================================ курсы
def test_rates_blocked_without_network_with_clear_message(rates_env, no_network, monkeypatch):
    hits = []
    monkeypatch.setattr(providers, "http_request", lambda *a, **kw: hits.append(a) or b"")
    t0 = time.monotonic()
    with pytest.raises(rates.RateUnavailable, match="закрытый контур: курсов «ЦБ РФ» на 2026-10-15 нет"):
        rates.get_rate("USD", "RUB", "2026-10-15")
    with pytest.raises(rates.RateUnavailable, match="Курсы из файла"):
        rates.refresh("cfets", "2026-10-15")
    rows = rates.list_rates("2026-10-15", "cbr")
    assert rows[0]["value"] is None and "закрытый контур" in rows[0]["note"]
    assert hits == [] and time.monotonic() - t0 < 1
    # ручной курс работает как раньше
    rates.set_manual("USD", "RUB", 84, date="2026-10-15")
    assert rates.get_rate("USD", "RUB", "2026-10-15").note.startswith("ручной: официальный недоступен")


def test_providers_http_request_guarded(contour, no_network):
    with pytest.raises(providers.SourceDown, match="Закрытый контур: обращение к www.cbr.ru"):
        providers.http_request("GET", providers.CBR_URL, params={"date_req": "15/10/2026"})


DYNAMIC = ('<?xml version="1.0" encoding="windows-1251"?><ValCurs ID="R01235" DateRange1="02.10.2026" '
           'DateRange2="06.10.2026" name="Foreign Currency Market Dynamic">'
           '<Record Date="02.10.2026" Id="R01235"><Nominal>1</Nominal><Value>81,1234</Value></Record>'
           '<Record Date="03.10.2026" Id="R01235"><Nominal>1</Nominal><Value>81,5000</Value></Record>'
           '<Record Date="06.10.2026" Id="R01235"><Nominal>1</Nominal><Value>82,0000</Value></Record>'
           '</ValCurs>').encode("cp1251")


def test_import_cbr_daily_and_dynamic(rates_env):
    with open(os.path.join(FIX, "cbr_2026-10-15.xml"), "rb") as fh:
        res = rfiles.import_file(fh.read(), "XML_daily.xml", user="olga")
    assert res["format"] == "cbr_daily" and "USD" in res["loaded"][0]["currencies"]
    info = rates.get_rate("USD", "RUB", "2026-10-15")
    assert info.value == 83.9497 and info.source == "cbr" and not info.manual
    assert rates.get_rate("JPY", "RUB", "2026-10-15").value == pytest.approx(0.529852)   # номинал 100
    res = rfiles.import_file(DYNAMIC, "dynamic.xml")
    assert res["format"] == "cbr_dynamic" and res["loaded"][0]["days"] == 5
    sat = rates.get_rate("USD", "RUB", "2026-10-05")          # выходные — курс от 03.10
    assert sat.value == 81.5 and sat.effective_date == "2026-10-03"
    after = rates.get_rate("USD", "RUB", "2026-10-07")        # после периода файла — с пометкой
    assert after.value == 82.0 and "курс из файла от 2026-10-06" in after.note
    with pytest.raises(rates.RateUnavailable, match="закрытый контур"):
        rates.get_rate("USD", "RUB", "2026-11-30")
    rec = [r for r in journal_records() if r["kind"] == "rate_import"]
    assert len(rec) == 2 and rec[0]["user"] == "olga"
    assert rec[0]["meta"]["sha256"] == hashlib.sha256(open(os.path.join(FIX, "cbr_2026-10-15.xml"), "rb").read()).hexdigest()
    rows = rates.list_rates("2026-10-15", "cbr")
    assert any("из файла XML_daily.xml" in r["note"] for r in rows)


def test_import_dynamic_unknown_code_needs_currency(rates_env):
    xml = DYNAMIC.replace(b"R01235", b"R09999")
    with pytest.raises(rfiles.RateFileError, match="укажите валюту"):
        rfiles.import_file(xml, "dyn.xml")
    res = rfiles.import_file(xml, "dyn.xml", currency="usd")
    assert res["loaded"][0]["currencies"] == ["USD"]


def test_import_cfets_csv_and_excel(rates_env):
    csv_text = ("日期,USD/CNY,EUR/CNY,100JPY/CNY,CNY/RUB\n"
                "2026-10-09,7.1000,8.2000,4.8000,11.5000\n"
                "2026-10-10,7.1100,---,4.8100,11.4000\n"
                "2026-10-13,7.1200,8.2200,4.8200,11.3000\n")
    res = rfiles.import_file(csv_text.encode("utf-8"), "ccpr.csv")
    assert res["format"] == "cfets_table"
    assert res["loaded"][0]["currencies"] == ["EUR", "JPY", "RUB", "USD"]
    assert rates.get_rate("USD", "CNY", "2026-10-13", side="cn").value == 7.12
    assert rates.get_rate("JPY", "CNY", "2026-10-09", side="cn").value == pytest.approx(0.048)
    assert rates.get_rate("RUB", "CNY", "2026-10-09", side="cn").value == pytest.approx(1 / 11.5)
    assert rates.get_rate("USD", "CNY", "2026-10-11", side="cn").value == 7.11       # выходной
    # Excel с заголовком не в первой строке (как выгрузка chinamoney)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["人民币汇率中间价历史数据"])
    ws.append([])
    ws.append(["日期", "USD/CNY", "HKD/CNY"])
    ws.append([dt.datetime(2026, 10, 14), 7.13, 0.915])
    buf = io.BytesIO()
    wb.save(buf)
    res = rfiles.import_file(buf.getvalue(), "cfets.xlsx")
    assert res["format"] == "cfets_table"
    assert rates.get_rate("HKD", "CNY", "2026-10-14", side="cn").value == 0.915


def test_import_template_excel_and_csv(rates_env):
    tpl = rfiles.template_xlsx()
    res = rfiles.import_file(tpl, "шаблон.xlsx", user="ivan")
    assert res["format"] == "template" and res["manual"] == 2
    assert rates.get_rate("USD", "RUB", "2026-10-15").value == 83.9497
    assert rates.get_rate("USD", "CNY", "2026-10-15", side="cn").value == 7.0965
    assert rates.get_rate("USD", "CNY", "2026-10-20", side="cn_customs").value == 7.1        # месяц
    assert rates.get_rate("KZT", "CNY", "2026-10-15", source="manual").value == 0.0145
    csv_text = ("дата;валюта;курс;источник;номинал;к валюте\n"
                "16.10.2026;EUR;92,5;ЦБ РФ;;\n"
                "16.10.2026;CNY;11,8;cbr;1;RUB\n"
                "16.10.2026;XXX;1;что-то;;\n"
                "16.10.2026;USD;abc;cbr;;\n")
    res = rfiles.import_file(csv_text.encode("cp1251"), "курсы.csv")
    assert len(res["warnings"]) == 2 and res["loaded"][0]["currencies"] == ["CNY", "EUR"]
    assert rates.get_rate("EUR", "CNY", "2026-10-16").value == pytest.approx(92.5 / 11.8)


def test_import_files_zip_and_errors(rates_env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("usd.xml", DYNAMIC)
        zf.writestr("readme.doc", b"x")
    out = rfiles.import_files([(buf.getvalue(), "флешка.zip"), (b"not a rates file", "x.csv"),
                               (b"", "empty.xml")])
    ok = [r for r in out if r["ok"]]
    bad = [r for r in out if not r["ok"]]
    assert len(ok) == 1 and ok[0]["file"] == "usd.xml"
    assert {r["file"] for r in bad} == {"x.csv", "empty.xml"}


def test_rates_import_api(rates_env, isolated_storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.server as server
    monkeypatch.setattr(server, "UPLOADS_DIR", str(tmp_path / "uploads"))
    c = TestClient(server.app)
    st = c.get("/api/offline").json()
    assert st["offline"] is True and "10.0.0.0/8" in st["allow_hosts"]
    with open(os.path.join(FIX, "cbr_2026-10-15.xml"), "rb") as fh:
        r = c.post("/api/rates/import", files=[("files", ("XML_daily.xml", fh.read())),
                                               ("files", ("bad.pdf", b"%PDF"))])
    d = r.json()
    assert r.status_code == 200 and d["processed"] == 2 and d["ok"] == 1
    assert c.get("/api/rates/convert", params={"amount": 10, "from_ccy": "USD", "to_ccy": "RUB",
                                               "date": "2026-10-15"}).json()["value"] == pytest.approx(839.497)
    t = c.get("/api/rates/template")
    assert t.status_code == 200 and t.content[:2] == b"PK"
    r = c.post("/api/rates/refresh", json={"source": "cbr", "date": "2026-10-20"})
    assert r.status_code == 400 and "Закрытый контур" in r.json()["detail"]


# ============================================================================ обновление с носителя
@pytest.fixture()
def media(contour, monkeypatch, rates_env):
    from app import media_update
    root = contour / "app"
    (root / "references").mkdir(parents=True)
    (root / "config" / "rules").mkdir(parents=True)
    (root / "references" / "hs_codes.csv").write_text("hs_code;name\n8471;old\n", encoding="utf-8")
    (root / "config" / "rules" / "price.yaml").write_text("tolerance: 0.01\n", encoding="utf-8")
    keys = root / "config" / "update_keys"
    keys.mkdir()
    monkeypatch.setattr(media_update, "PROJECT_ROOT", str(root))
    monkeypatch.setattr(media_update, "UPDATES_DIR", str(root / "storage" / "updates"))
    monkeypatch.setattr(media_update, "_validate_after", lambda: None)
    settings = {"require_signature": False, "keys_dir": str(keys)}
    monkeypatch.setattr(media_update, "update_settings", lambda cfg=None: dict(settings))
    return types.SimpleNamespace(mu=media_update, root=root, keys=keys, settings=settings)


def _pkg(m, files, **kw):
    return m.mu.build_package(files, kw.get("text", "HS-коды на ноябрь"), kw.get("key"))


def test_media_update_verify_and_apply(media):
    files = {"references/hs_codes.csv": "hs_code;name\n8471;new\n".encode("utf-8"),
             "references/contracts.xlsx": b"PK-fake",
             "config/rules/price.yaml": b"tolerance: 0.02\n",
             "rates/usd.xml": DYNAMIC}
    data = _pkg(media, files)
    rep = media.mu.verify(data, "update.zip")
    assert rep["ok"] and not rep["errors"] and rep["signature"]["signed"] is False
    acts = {f["path"]: f["action"] for f in rep["files"]}
    assert acts["references/hs_codes.csv"] == "заменить" and acts["references/contracts.xlsx"] == "добавить"
    assert acts["rates/usd.xml"] == "курсы" and rep["description"] == "HS-коды на ноябрь"
    assert (media.root / "references" / "hs_codes.csv").read_text(encoding="utf-8").endswith("old\n")
    res = media.mu.apply(data, "update.zip", user="admin")
    assert res["applied"] and (media.root / "references" / "hs_codes.csv").read_text(encoding="utf-8").endswith("new\n")
    assert (media.root / "config" / "rules" / "price.yaml").read_text() == "tolerance: 0.02\n"
    backup = media.root / res["backup"] / "references" / "hs_codes.csv"
    assert backup.read_text(encoding="utf-8").endswith("old\n")
    assert res["rates"][0]["ok"] and rates.get_rate("USD", "RUB", "2026-10-06").value == 82.0
    rec = [r for r in journal_records() if r["kind"] == "media_update"]
    assert len(rec) == 1 and rec[0]["meta"]["ok"] and rec[0]["meta"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert "контрольные суммы совпали" in rec[0]["detail"]
    assert media.mu.history()[0]["kind"] == "media_update"


def _rezip(data: bytes, change) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    items = {i.filename: src.read(i.filename) for i in src.infolist()}
    change(items)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for k, v in items.items():
            zf.writestr(k, v)
    return out.getvalue()


def test_media_update_rejects_tampered_and_forbidden(media):
    good = _pkg(media, {"references/hs_codes.csv": b"hs_code;name\n1;x\n"})
    tampered = _rezip(good, lambda d: d.__setitem__("references/hs_codes.csv", b"hs_code;name\n1;EVIL\n"))
    rep = media.mu.verify(tampered)
    assert not rep["ok"] and any("контрольная сумма не совпадает" in e for e in rep["errors"])
    extra = _rezip(good, lambda d: d.__setitem__("references/extra.csv", b"a\n"))
    assert any("нет в SHA256SUMS" in e for e in media.mu.verify(extra)["errors"])
    missing = _rezip(good, lambda d: d.pop("references/hs_codes.csv"))
    assert any("в архиве его нет" in e for e in media.mu.verify(missing)["errors"])
    no_sums = _rezip(good, lambda d: d.pop("SHA256SUMS"))
    assert any("нет SHA256SUMS" in e for e in media.mu.verify(no_sums)["errors"])
    for path in ("config/system.yaml", "app/server.py", "config/rules/sub/x.yaml"):
        rep = media.mu.verify(_pkg(media, {path: b"a: 1\n"}))
        assert not rep["ok"], path
    bad_yaml = media.mu.verify(_pkg(media, {"config/rules/price.yaml": b"a: [1,\n"}))
    assert any("YAML не читается" in e for e in bad_yaml["errors"])
    slip = io.BytesIO()
    with zipfile.ZipFile(slip, "w") as zf:
        zf.writestr("../evil.csv", b"x")
        zf.writestr("SHA256SUMS", hashlib.sha256(b"x").hexdigest() + "  ../evil.csv\n")
    assert any("небезопасный путь" in e for e in media.mu.verify(slip.getvalue())["errors"])
    with pytest.raises(media.mu.UpdateError):
        media.mu.apply(tampered, "t.zip", user="admin")
    assert (media.root / "references" / "hs_codes.csv").read_text(encoding="utf-8").endswith("old\n")
    rec = [r for r in journal_records() if r["kind"] == "media_update"]
    assert rec and rec[-1]["meta"]["ok"] is False and "отклонено" in rec[-1]["detail"]


def test_media_update_rollback_when_config_breaks(media, monkeypatch):
    def broken():
        raise ValueError("правила не читаются")
    monkeypatch.setattr(media.mu, "_validate_after", broken)
    data = _pkg(media, {"references/hs_codes.csv": b"hs_code;name\n1;new\n", "references/new.csv": b"a\n"})
    with pytest.raises(media.mu.UpdateError, match="всё возвращено назад"):
        media.mu.apply(data, user="admin")
    assert (media.root / "references" / "hs_codes.csv").read_text(encoding="utf-8").endswith("old\n")
    assert not (media.root / "references" / "new.csv").exists()


def test_media_update_signature(media):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    def key_pair():
        k = Ed25519PrivateKey.generate()
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
        pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return pem, base64.b64encode(pub).decode()

    ours, ours_pub = key_pair()
    theirs, _ = key_pair()
    (media.keys / "team.pub").write_text(ours_pub + "\n")
    files = {"references/hs_codes.csv": b"hs_code;name\n1;signed\n"}
    rep = media.mu.verify(_pkg(media, files, key=ours))
    assert rep["ok"] and rep["signature"]["valid"] and rep["signature"]["key"] == "team.pub"
    rep = media.mu.verify(_pkg(media, files, key=theirs))
    assert not rep["ok"] and any("не подходит ни к одному доверенному ключу" in e for e in rep["errors"])
    media.settings["require_signature"] = True
    rep = media.mu.verify(_pkg(media, files))
    assert not rep["ok"] and any("без подписи" in e for e in rep["errors"])
    assert media.mu.apply(_pkg(media, files, key=ours), user="admin")["signature"]["valid"]
    # подписанный архив изменён после подписи (SHA256SUMS пересчитан, подпись старая)
    signed = _pkg(media, files, key=ours)

    def swap(d):
        d["references/hs_codes.csv"] = b"hs_code;name\n1;evil\n"
        d["SHA256SUMS"] = (hashlib.sha256(d["references/hs_codes.csv"]).hexdigest() + "  references/hs_codes.csv\n"
                           + hashlib.sha256(d["UPDATE.txt"]).hexdigest() + "  UPDATE.txt\n").encode()
    assert not media.mu.verify(_rezip(signed, swap))["ok"]


def test_media_update_api_admin_only(media, isolated_storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.server as server
    from app import security
    monkeypatch.setattr(server, "UPLOADS_DIR", str(tmp_path / "uploads"))
    c = TestClient(server.app)
    data = _pkg(media, {"references/hs_codes.csv": b"hs_code;name\n1;api\n"})
    r = c.post("/api/offline/update", files={"file": ("u.zip", data)})
    assert r.status_code == 200 and r.json()["ok"] and "applied" not in r.json()
    r = c.post("/api/offline/update?apply=true", files={"file": ("u.zip", data)})
    assert r.status_code == 200 and r.json()["applied"]
    assert c.get("/api/offline/updates").json()[0]["kind"] == "media_update"
    monkeypatch.setattr(server, "_auth_disabled", lambda: False)
    monkeypatch.setattr(security, "get_session", lambda t: {"login": "op", "role": "operator"} if t else None)
    r = c.post("/api/offline/update", files={"file": ("u.zip", data)}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 403


# ============================================================================ интерфейс и PDF
EXTERNAL = re.compile(r"""(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s"'<>)]*""")


def test_webui_has_no_external_resources():
    webui = os.path.join(ROOT, "webui")
    found = []
    for name in os.listdir(webui):
        if not name.endswith((".html", ".js", ".css")):
            continue
        text = open(os.path.join(webui, name), encoding="utf-8").read()
        for m in re.finditer(r"https?://[^\s\"'<>)]+", text):
            if m.group(0).startswith("http://www.w3.org/2000/svg"):
                continue
            found.append(f"{name}: {m.group(0)}")
        for m in re.finditer(r"""(?:src|href)\s*=\s*["']//""", text):
            found.append(f"{name}: адрес без схемы {m.group(0)}")
        for m in re.finditer(r"@import|fonts\.googleapis|cdn\.|unpkg|jsdelivr", text):
            found.append(f"{name}: {m.group(0)}")
    assert not found, "внешние ресурсы в интерфейсе: " + "; ".join(found)
    index = open(os.path.join(webui, "index.html"), encoding="utf-8").read()
    assert '<script src="offline.js"></script>' in index and 'id="netChip"' in index


def test_pdf_fonts_are_local():
    from app.services import report_pdf
    assert os.path.isfile(os.path.join(ROOT, "assets", "fonts", "DejaVuSans.ttf"))
    assert os.path.isfile(os.path.join(ROOT, "assets", "fonts", "DejaVuSans-Bold.ttf"))
    assert report_pdf._LATIN_CANDIDATES[0][0].startswith(os.path.join(ROOT, "assets", "fonts"))
    assert report_pdf.FONT == "AppFont" and report_pdf.FONT_B == "AppFontBold"
    from reportlab.pdfbase import pdfmetrics
    face = pdfmetrics.getFont("AppFont").face
    assert os.path.abspath(face.filename) == os.path.join(ROOT, "assets", "fonts", "DejaVuSans.ttf")


def test_ocr_models_local():
    from app.services import ocr_reader
    for lang in ("rus", "eng", "chi_sim"):
        assert os.path.isfile(os.path.join(ROOT, "tessdata", f"{lang}.traineddata"))
    assert ocr_reader._tessdata_dir() in (os.path.join(ROOT, "tessdata"), os.environ.get("TESSDATA_PREFIX"))


# ============================================================================ комплект без интернета
BUNDLE = os.path.join(ROOT, "tools", "make_offline_bundle.py")


def _run(args, **kw):
    return subprocess.run([sys.executable, BUNDLE] + args, capture_output=True, text=True, timeout=120, **kw)


def test_bundle_dry_run_plans_everything_creates_nothing(tmp_path):
    out = tmp_path / "dist"
    r = _run(["--dry-run", "--platforms", "windows,linux-x86_64,mac-arm64", "--python", "3.11,3.12",
              "--with-ocr", "--with-python", "3.12.10", "--with-docker", "--out", str(out)])
    assert r.returncode == 0, r.stderr
    plan = r.stdout
    assert not out.exists()
    assert "pip wheel --no-deps -w wheelhouse/any proxy_tools" in plan
    for plat, py in (("win_amd64", "3.11"), ("win_amd64", "3.12"), ("manylinux2014_x86_64", "3.12"),
                     ("macosx_11_0_arm64", "3.11")):
        assert re.search(rf"pip download --only-binary=:all: --implementation cp --python-version {py} "
                         rf".*--platform {plat}", plan), (plat, py)
    assert "wheelhouse/windows-py3.11" in plan and "wheelhouse/mac-arm64-py3.12" in plan
    assert " pythonnet" in plan and " pyobjc-framework-Cocoa" in plan and " colorama" in plan
    assert "tesseract-ocr-w64-setup" in plan and "micromamba-osx-arm64" in plan
    assert "--platform osx-arm64" in plan and "python-3.12.10-amd64.exe" in plan
    assert "python-3.12.10-macos11.pkg" in plan and "docker save postcontrol-suite:latest" in plan
    assert "Ничего не создано" in plan


def _fake_wheel(folder, name="sibdoc_fake", version="0.1"):
    folder.mkdir(parents=True, exist_ok=True)
    di = f"{name}-{version}.dist-info"
    files = {f"{name}/__init__.py": b"OK = True\n",
             f"{di}/METADATA": f"Metadata-Version: 2.1\nName: {name.replace('_', '-')}\nVersion: {version}\n".encode(),
             f"{di}/WHEEL": b"Wheel-Version: 1.0\nGenerator: sibdoc-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    rec = [f"{p},sha256={base64.urlsafe_b64encode(hashlib.sha256(b).digest()).rstrip(b'=').decode()},{len(b)}"
           for p, b in files.items()] + [f"{di}/RECORD,,"]
    files[f"{di}/RECORD"] = ("\n".join(rec) + "\n").encode()
    path = folder / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for p, b in files.items():
            zf.writestr(p, b)
    return path


def test_bundle_from_fake_wheelhouse_archive_and_check(tmp_path):
    app = tmp_path / "app"
    (app / "tessdata").mkdir(parents=True)
    (app / "tessdata" / "rus.traineddata").write_bytes(b"model")
    (app / "requirements.txt").write_text("sibdoc-fake==0.1\n")
    shutil.copy(os.path.join(ROOT, "install.sh"), app / "install.sh")
    (app / "storage").mkdir()
    (app / "storage" / "journal.jsonl").write_text("secret\n")         # рабочие данные в комплект не идут
    wh = tmp_path / "wh"
    _fake_wheel(wh / "linux-x86_64-py3.12")
    out = tmp_path / "dist"
    r = _run(["--src", str(app), "--wheelhouse", str(wh), "--out", str(out), "--name", "kit",
              "--cjk-font", "/nonexistent.ttc"])
    assert r.returncode == 0, r.stdout + r.stderr
    kit = out / "kit"
    assert (kit / "wheelhouse" / "linux-x86_64-py3.12" / "sibdoc_fake-0.1-py3-none-any.whl").is_file()
    assert (kit / "tessdata" / "rus.traineddata").is_file() and not (kit / "storage").exists()
    man = json.loads((kit / "offline" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert man["wheelhouse"] == ["linux-x86_64-py3.12"] and man["product"] == "SibDoc 3"
    assert "install.sh --offline wheelhouse" in (kit / "ЗАКРЫТЫЙ-КОНТУР.txt").read_text(encoding="utf-8")
    sums = (kit / "SHA256SUMS").read_text()
    assert "  wheelhouse/linux-x86_64-py3.12/sibdoc_fake-0.1-py3-none-any.whl" in sums
    with zipfile.ZipFile(out / "kit.zip") as zf:
        names = zf.namelist()
        assert "kit/SHA256SUMS" in names and "kit/install.sh" in names
        assert (zf.getinfo("kit/install.sh").external_attr >> 16) & 0o111          # исполняемый
    assert _run(["--check", str(kit)]).returncode == 0
    (kit / "tessdata" / "rus.traineddata").write_bytes(b"broken")
    r = _run(["--check", str(kit)])
    assert r.returncode == 1 and "rus.traineddata" in r.stdout


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="нужен bash")
def test_install_sh_offline_uses_only_wheelhouse(tmp_path):
    try:
        import ensurepip  # noqa: F401
    except ImportError:
        pytest.skip("нет ensurepip для venv")
    app = tmp_path / "kit"
    app.mkdir()
    shutil.copy(os.path.join(ROOT, "install.sh"), app / "install.sh")
    (app / "requirements.txt").write_text("sibdoc-fake==0.1\n")
    _fake_wheel(app / "wheelhouse" / "linux-x86_64-py3.12")
    env = {**os.environ, "SIBDOC_NO_DOCKER": "1", "PIP_INDEX_URL": "http://127.0.0.1:9/simple",
           "PIP_CONFIG_FILE": os.devnull}
    r = subprocess.run(["bash", "install.sh", "--offline", "wheelhouse"], cwd=app, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "без интернета" in r.stdout
    py = app / ".venv" / "bin" / "python"
    assert subprocess.run([str(py), "-c", "import sibdoc_fake; assert sibdoc_fake.OK"]).returncode == 0
    r = subprocess.run(["bash", "install.sh", "--offline", "nowhere"], cwd=app, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 1 and "нет библиотек *.whl" in r.stdout
