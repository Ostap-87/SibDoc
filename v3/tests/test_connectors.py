# -*- coding: utf-8 -*-
"""Тесты коннектов app/connectors — без сети и без реальных серверов.

IMAP — фейковый сервер (monkeypatch imaplib.IMAP4_SSL/IMAP4), письма с китайскими
именами вложений (RFC 2047 / RFC 2231, GB2312/GBK/UTF-8) и zip (cp866/GBK-имена,
zip-slip, zip-бомба). HTTP — подмена requests.Session.request. SFTP — фейковый
модуль paramiko в sys.modules. Журнал и состояние — во временной папке.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import imaplib
import io
import json
import os
import stat
import sys
import types
import zipfile
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import requests  # noqa: E402

import app.journal as journal  # noqa: E402
from app import connectors  # noqa: E402
from app.connectors import base as cbase  # noqa: E402
from app.connectors import ConnectorError, catalog, get_connector, list_connectors  # noqa: E402
from app.connectors import fileutil  # noqa: E402
from app.connectors.imap_mail import imap_utf7  # noqa: E402
from app.connectors.yonyou import yonyou_signature  # noqa: E402

PASSWORD = "S3cr3t-Пароль!42"
SLOT_RULES = [
    {"pattern": r"(?i)(报关单|customs.?dec|cn_dec)", "slot": "cn_dec"},
    {"pattern": r"(?i)((?<![a-zа-яё])ДТ(?![a-zа-яё])|ESADout|gtd|ru_dt)", "slot": "ru_dt"},
    {"pattern": r"(?i)\.xml$", "sender": r"(?i)@broker-ru\.example$", "slot": "ru_dt"},
]


# ============================================================================ fixtures
@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(cbase, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SIBDOC_TEST_PASSWORD", PASSWORD)
    yield


def journal_records() -> list[dict]:
    if not os.path.exists(journal.JOURNAL_FILE):
        return []
    with open(journal.JOURNAL_FILE, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def journal_text() -> str:
    if not os.path.exists(journal.JOURNAL_FILE):
        return ""
    with open(journal.JOURNAL_FILE, encoding="utf-8") as fh:
        return fh.read()


class FakeResp:
    def __init__(self, data=None, status=200, text=None):
        self._data = data
        self.status_code = status
        self.text = text if text is not None else (json.dumps(data, ensure_ascii=False)
                                                   if data is not None else "")
        self.headers = {}

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")


@pytest.fixture
def http(monkeypatch):
    """Подмена HTTP: http.routes = [(predicate(method, url, kw), response|callable)]."""
    state = types.SimpleNamespace(calls=[], routes=[])

    def fake(self, method, url, **kw):
        method = method.upper()
        call = {"method": method, "url": url, "params": dict(kw.get("params") or {}),
                "json": kw.get("json"), "data": kw.get("data"),
                "headers": dict(self.headers), "auth": self.auth}
        state.calls.append(call)
        for pred, resp in state.routes:
            if pred(method, url, call):
                return resp(call) if callable(resp) else resp
        raise requests.ConnectionError(f"нет связи (тест): {method} {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", fake)
    return state


# ============================================================================ base / secrets
def test_secret_from_env_and_var_and_literal_not_exposed(monkeypatch):
    monkeypatch.setenv("SIBDOC_TEST_TOKEN", "tok-from-env-123")
    c = connectors.RestConnector("api", {
        "type": "rest", "url": "https://${SIBDOC_TEST_HOST}/v1", "slot": "purchase",
        "auth": "basic", "username": "svc", "password": "literal-pass-999",
        "token": "${SIBDOC_TEST_TOKEN}", "api_key_env": "SIBDOC_TEST_PASSWORD",
        "headers": {"X-Trace": "1", "Authorization": "Bearer hardcoded-xyz"}})
    monkeypatch.setenv("SIBDOC_TEST_HOST", "api.example.test")
    assert c.secret("password") == "literal-pass-999"         # литерал работает
    assert c.secret("token") == "tok-from-env-123"            # ${VAR}
    assert c.secret("api_key") == PASSWORD                     # *_env
    assert c.opt("url") == "https://api.example.test/v1"
    pd = c.public_dict()
    dumped = json.dumps(pd, ensure_ascii=False)
    for s in ("literal-pass-999", "tok-from-env-123", PASSWORD, "hardcoded-xyz"):
        assert s not in dumped
    by_key = {p["key"]: p for p in pd["params"]}
    assert by_key["password"]["set"] is True and by_key["password"]["source"] == "literal"
    assert by_key["api_key"]["source"] == "env" and by_key["api_key"]["env"] == "SIBDOC_TEST_PASSWORD"
    assert by_key["url"]["value"] == "https://${SIBDOC_TEST_HOST}/v1"   # шаблон, не значение
    assert any("открытым текстом" in w for w in pd["warnings"])
    assert pd["stage"] == "ready" and pd["enabled"] is False
    assert {"id", "type", "title", "label", "enabled", "stage", "params", "missing"} <= set(pd)


def test_missing_and_redact():
    c = connectors.ImapMailConnector("mail", {"type": "imap", "host": "imap.x",
                                              "password_env": "NOPE_NOT_SET"})
    assert c.missing() == ["username", "password"]
    res = c.test(user="tester")
    assert res["ok"] is False and "username" in res["detail"]
    rec = journal_records()[-1]
    assert rec["kind"] == "connector_test" and rec["user"] == "tester"

    c2 = connectors.RestConnector("r", {"password_env": "SIBDOC_TEST_PASSWORD"})
    assert PASSWORD not in c2.redact(f"ошибка: login failed for pass={PASSWORD}")
    assert "***" in c2.redact("https://user:pw123456@host/x")


def test_slot_rules_and_prefix():
    c = connectors.FolderConnector("f", {"slots": ["wide_table", "purchase"],
                                         "slot_rules": SLOT_RULES})
    assert c.slot_for("报关单_2026-10.xlsx") == "cn_dec"
    assert c.slot_for("Customs_Declaration_1022.csv") == "cn_dec"
    assert c.slot_for("ДТ_10702070_221026_0001234.xml") == "ru_dt"
    assert c.slot_for("ДТ 10702070.xlsx") == "ru_dt"
    assert c.slot_for("ESADout_CU.xml") == "ru_dt"
    assert c.slot_for("ДТАБЛИЦА.xlsx") is None                   # «ДТ» внутри слова
    assert c.slot_for("export.xml", sender="d@broker-ru.example") == "ru_dt"
    assert c.slot_for("export.xml", sender="d@other.example") is None
    assert c.slot_for("wide_table_2026-10-06.xlsx") == "wide_table"
    assert c.slot_for("报关单.pdf") is None                       # PDF — без слота
    assert connectors.Connector._slot_for_name("purchase-1.xlsx", ["purchase"]) == "purchase"


def test_catalog_and_registry_defaults_disabled():
    types_ = {e["type"]: e for e in catalog()}
    for t in ("folder", "sftp", "imap", "rest", "database", "sap_odata"):
        assert types_[t]["stage"] == "ready"
    for t in ("onec_odata", "etran", "cn_single_window", "kingdee", "yonyou", "bank_fx_control"):
        assert types_[t]["stage"] == "needs_access", t
        assert types_[t]["access_hint"]
    for e in types_.values():
        assert e["title"] and e["params"]
        for p in e["params"]:
            assert {"key", "label", "required", "secret", "example"} <= set(p)
    cfg = {"connectors": {t: {"type": t} for t in types_}}
    cfg["connectors"]["unknown"] = {"type": "nope"}
    lst = list_connectors(cfg)
    assert len(lst) == len(types_) and not any(c.enabled for c in lst)
    assert get_connector(cfg, "imap").type == "imap"
    assert get_connector(cfg, "missing") is None


# ============================================================================ needs_access stubs
@pytest.mark.parametrize("ctype", ["onec_odata", "etran", "cn_single_window", "kingdee",
                                   "yonyou", "bank_fx_control"])
def test_stubs_need_access(ctype, tmp_path):
    c = connectors.TYPES[ctype](ctype, {"type": ctype})
    assert c.enabled is False
    res = c.test(user="ivanov")
    assert res["ok"] is False
    assert res["detail"].startswith("Задел: нужен доступ — ")
    with pytest.raises(ConnectorError) as ei:
        c.pull(str(tmp_path / "out"), user="ivanov")
    assert str(ei.value).startswith("Задел: нужен доступ — ")
    kinds = [r["kind"] for r in journal_records()]
    assert kinds == ["connector_test", "connector_error"]
    assert all(r["user"] == "ivanov" for r in journal_records())


def test_stub_skeleton_and_export_dir(tmp_path):
    c = connectors.EtranConnector("etran", {"service_url": "https://etran.example",
                                            "login": "u", "password_env": "SIBDOC_TEST_PASSWORD",
                                            "cert_thumbprint": "AB"})
    res = c.test()
    assert res["ok"] is False and res["detail"].startswith("Каркас:")
    assert "уточнить у владельца системы" in res["detail"]
    exp = tmp_path / "exp"
    exp.mkdir()
    (exp / "报关单_1.xlsx").write_bytes(b"x")
    sw = connectors.CnSingleWindowConnector("sw", {"export_dir": str(exp), "pattern": "*.xlsx",
                                                   "slot_rules": SLOT_RULES})
    assert sw.test()["ok"] is True
    got = sw.pull(str(tmp_path / "out"))
    assert got[0]["slot"] == "cn_dec" and os.path.basename(got[0]["path"]) == "cn_dec_报关单_1.xlsx"


# ============================================================================ zip / encodings
def _zip_with_raw_names(entries: list[tuple[bytes, bytes]]) -> bytes:
    """zip с именами в «сырой» кодировке (cp866/GBK без флага UTF-8), как пишет Windows."""
    placeholders = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (raw, data) in enumerate(entries):
            ph = (f"{i:03d}" + "#" * len(raw))[:len(raw)]
            assert len(ph) == len(raw)
            placeholders.append((ph.encode("ascii"), raw))
            zf.writestr(ph, data)
    out = buf.getvalue()
    for ph, raw in placeholders:
        assert out.count(ph) == 2
        out = out.replace(ph, raw)
    return out


def test_extract_zip_names_slip_and_bomb():
    data = _zip_with_raw_names([
        ("ДТ_10702070.xml".encode("cp866"), b"<ESADout/>"),
        ("报关单_明细.xlsx".encode("gbk"), b"xlsx"),
        (b"../../evil.xml", b"<x/>"),
        (b"bomb.csv", b"0" * (3 * 2**20)),
    ])
    entries, notes = fileutil.extract_zip(data)
    names = [n for n, _ in entries]
    assert names == ["ДТ_10702070.xml", "报关单_明细.xlsx"]
    assert any("небезопасный путь" in n for n in notes)
    assert any("zip-бомбу" in n for n in notes)
    with pytest.raises(ValueError):
        fileutil.extract_zip(data, max_total=5)
    with pytest.raises(ValueError):
        fileutil.extract_zip(b"not a zip")


def test_csv_transcoding():
    gbk = "报关单号,金额\n222620261000123,100\n".encode("gbk")
    out, enc = fileutil.csv_to_utf8(gbk)
    assert enc == "gb18030" and out.decode("utf-8-sig").startswith("报关单号")
    ru = "Номер ДТ,Цена\n10702070/221026/0001234,9661\nя,1\n".encode("cp1251")
    out, enc = fileutil.csv_to_utf8(ru)
    assert enc == "cp1251" and out.decode("utf-8-sig").startswith("Номер ДТ")


def test_imap_utf7():
    assert imap_utf7("INBOX") == "INBOX"
    assert imap_utf7("Входящие") == "&BBIERQQ+BDQETwRJBDgENQ-"
    assert imap_utf7("A&B") == "A&-B"


# ============================================================================ IMAP
def _b64word(text: str, charset: str) -> str:
    return f"=?{charset}?B?{base64.b64encode(text.encode(charset)).decode()}?="


def _attach(msg, data: bytes, disposition: str):
    part = MIMEApplication(data, "octet-stream")
    del part["Content-Disposition"]
    part["Content-Disposition"] = disposition
    msg.attach(part)


def build_mailbox() -> dict[int, dict]:
    m1 = MIMEMultipart()
    m1["From"] = f"{_b64word('中国报关行', 'gb2312')} <declarant@cn-broker.example>"
    m1["Subject"] = _b64word("报关单 10月", "gb2312")
    m1["Message-ID"] = "<m1@cn-broker.example>"
    m1.attach(MIMEText("请查收", "plain", "utf-8"))
    _attach(m1, b"xlsx-1", f'attachment; filename="{_b64word("报关单_10月.xlsx", "gb2312")}"')
    csv_gbk = "报关单号,金额\n222620261000123,100\n".encode("gbk")
    part = MIMEApplication(csv_gbk, "octet-stream")
    del part["Content-Disposition"]
    part.add_header("Content-Disposition", "attachment", filename=("gbk", "", "报关单明细.csv"))
    m1.attach(part)
    _attach(m1, b"%PDF-1.4", f'attachment; filename="{_b64word("发票.pdf", "utf-8")}"')
    _attach(m1, b"docx", 'attachment; filename="notes.docx"')
    zip_cn = _zip_with_raw_names([("报关单_2.xlsx".encode("gbk"), b"xlsx-2")])
    _attach(m1, zip_cn, f'attachment; filename="{_b64word("资料.zip", "gbk")}"')

    m2 = MIMEMultipart()
    m2["From"] = f"{Header('Декларант', 'utf-8').encode()} <declarant@broker-ru.example>"
    m2["Subject"] = Header("ДТ за октябрь", "utf-8").encode()
    m2["Message-ID"] = "<m2@broker-ru.example>"
    zip_ru = _zip_with_raw_names([
        ("ДТ_10702070_221026_0001234.xml".encode("cp866"), b"<ESADout_CU/>"),
        (b"../../evil.xml", b"<x/>"),
        (b"sub/ESADout_CU.xml", b"<ESADout_CU/>"),
        (b"bomb.csv", b"0" * (3 * 2**20)),
        (b"readme.txt", b"txt"),
    ])
    _attach(m2, zip_ru, 'attachment; filename="docs.zip"')
    _attach(m2, b"<xml/>", 'attachment; filename="export.xml"')

    m3 = MIMEMultipart()
    m3["From"] = "spam@other.example"
    m3["Subject"] = _b64word("报关单", "utf-8")
    m3["Message-ID"] = "<m3@other.example>"
    _attach(m3, b"x", 'attachment; filename="报关单.xlsx"')

    m4 = MIMEMultipart()
    m4["From"] = "declarant@cn-broker.example"
    m4["Subject"] = "Hello"
    m4["Message-ID"] = "<m4@cn-broker.example>"
    _attach(m4, b"x", 'attachment; filename="other.xlsx"')

    return {uid: {"raw": m.as_bytes(), "flags": set()}
            for uid, m in ((11, m1), (12, m2), (13, m3), (14, m4))}


class FakeIMAP:
    mailbox: dict = {}
    instances: list = []
    folders = {'"INBOX"', '"SibDoc/&BB4EMQRABDAEMQQ+BEIEMAQ9BD4-"'}

    def __init__(self, host, port=None, ssl_context=None, timeout=None):
        self.host, self.port = host, port
        self.calls: list = []
        self.capabilities = ("IMAP4REV1", "MOVE")
        FakeIMAP.instances.append(self)

    def starttls(self, ssl_context=None):
        self.calls.append(("STARTTLS",))

    def login(self, user, password):
        password.encode("ascii")              # как настоящий imaplib: LOGIN только ASCII
        if password != PASSWORD:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        self.calls.append(("LOGIN", user))
        return "OK", [b"ok"]

    def authenticate(self, mechanism, authobject):
        _, user, password = authobject(b"").split(b"\0")
        if mechanism != "PLAIN" or password.decode("utf-8") != PASSWORD:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        self.calls.append(("AUTHENTICATE", user.decode("utf-8")))
        return "OK", [b"ok"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("SELECT", mailbox, readonly))
        if mailbox not in self.folders:
            return "NO", [b"no such mailbox"]
        return "OK", [str(len(self.mailbox)).encode()]

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        self.calls.append((cmd,) + args)
        if cmd == "SEARCH":
            crit = args[1:]
            uids = [u for u, m in sorted(self.mailbox.items())
                    if not ("UNSEEN" in crit and "\\Seen" in m["flags"])]
            return "OK", [" ".join(map(str, uids)).encode()]
        if cmd == "FETCH":
            out = []
            for i, u in enumerate(args[0].split(",")):
                u = int(u)
                if u not in self.mailbox:
                    continue
                raw = self.mailbox[u]["raw"]
                if "HEADER.FIELDS" in args[1]:
                    hdr = raw.split(b"\n\n", 1)[0] + b"\n\n"
                    if u == 12:   # UID после литерала — тоже бывает
                        out += [(f"{i + 1} (BODY[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)] "
                                 f"{{{len(hdr)}}}".encode(), hdr), f" UID {u})".encode()]
                    else:
                        out += [(f"{i + 1} (UID {u} BODY[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID "
                                 f"DATE)] {{{len(hdr)}}}".encode(), hdr), b")"]
                else:
                    out += [(f"{i + 1} (UID {u} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
            return "OK", out
        if cmd == "STORE":
            self.mailbox[int(args[0])]["flags"].add(args[2].strip("()"))
            return "OK", [b""]
        if cmd == "MOVE":
            if args[1] not in self.folders:
                return "NO", [b"no such mailbox"]
            self.mailbox.pop(int(args[0]), None)
            return "OK", [b""]
        return "OK", [b""]

    def expunge(self):
        self.calls.append(("EXPUNGE",))

    def logout(self):
        self.calls.append(("LOGOUT",))


@pytest.fixture
def imap(monkeypatch):
    FakeIMAP.mailbox = build_mailbox()
    FakeIMAP.instances = []
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(imaplib, "IMAP4", FakeIMAP)
    return FakeIMAP


def imap_cfg(**kw) -> dict:
    cfg = {"type": "imap", "enabled": True, "host": "imap.company.test", "username": "sibdoc",
           "password_env": "SIBDOC_TEST_PASSWORD", "folder": "INBOX",
           "from": ["@cn-broker.example", "declarant@broker-ru.example"],
           "subject_contains": ["报关单", "ДТ"], "since_days": 14, "slot_rules": SLOT_RULES}
    cfg.update(kw)
    return cfg


def test_imap_test_counts_matching_letters(imap):
    c = connectors.ImapMailConnector("mail", imap_cfg())
    res = c.test(user="petrov")
    assert res["ok"] is True, res
    assert res["messages"] == 2
    assert "подходящих писем 2" in res["detail"]
    conn = imap.instances[-1]
    assert ("AUTHENTICATE", "sibdoc") in conn.calls      # пароль с кириллицей — AUTH PLAIN
    assert ("SELECT", '"INBOX"', True) in conn.calls
    search = [c for c in conn.calls if c[0] == "SEARCH"][0]
    assert "SINCE" in search
    assert journal_records()[-1]["kind"] == "connector_test"


def test_imap_bad_password_and_folder(imap, monkeypatch):
    monkeypatch.setenv("SIBDOC_WRONG", "wrong-password-777")
    c = connectors.ImapMailConnector("mail", imap_cfg(password_env="SIBDOC_WRONG"))
    res = c.test()
    assert res["ok"] is False and "вход не выполнен" in res["detail"]
    assert "wrong-password-777" not in res["detail"] + journal_text()
    c = connectors.ImapMailConnector("mail", imap_cfg(folder="Нет такой"))
    res = c.test()
    assert res["ok"] is False and "папка" in res["detail"]


def test_imap_pull_chinese_names_zip_and_dedup(imap, tmp_path):
    dest = str(tmp_path / "uploads")
    c = connectors.ImapMailConnector("mail", imap_cfg())
    got = c.pull(dest, user="petrov")
    by_file = {i["file"]: i for i in got}
    assert set(by_file) == {"报关单_10月.xlsx", "报关单明细.csv", "发票.pdf", "报关单_2.xlsx",
                            "ДТ_10702070_221026_0001234.xml", "ESADout_CU.xml", "export.xml"}
    assert by_file["报关单_10月.xlsx"]["slot"] == "cn_dec"
    assert os.path.basename(by_file["报关单_10月.xlsx"]["path"]) == "cn_dec_报关单_10月.xlsx"
    assert by_file["报关单_2.xlsx"]["slot"] == "cn_dec"                      # из zip, имя GBK
    assert by_file["发票.pdf"]["slot"] is None
    assert by_file["ДТ_10702070_221026_0001234.xml"]["slot"] == "ru_dt"     # из zip, имя cp866
    assert by_file["ESADout_CU.xml"]["slot"] == "ru_dt"
    assert by_file["export.xml"]["slot"] == "ru_dt"                          # правило по отправителю
    csv_item = by_file["报关单明细.csv"]
    assert csv_item["slot"] == "cn_dec" and csv_item["encoding"] == "gb18030"
    with open(csv_item["path"], encoding="utf-8-sig") as fh:
        assert list(csv.reader(fh))[0] == ["报关单号", "金额"]
    assert not any("evil" in os.path.basename(i["path"]) for i in got)
    assert all(os.path.dirname(i["path"]) == dest for i in got)              # ничего вне dest
    assert any("небезопасный путь" in n for n in c.last_notes)
    assert any("zip-бомбу" in n for n in c.last_notes)
    seen_file = os.path.join(cbase.STATE_DIR, "mail.seen.json")
    assert os.path.exists(seen_file)
    seen = json.load(open(seen_file, encoding="utf-8"))
    assert "<m1@cn-broker.example>|报关单_10月.xlsx" in seen
    # письма не тронуты (after_success: none, папка открыта только на чтение)
    assert all(not m["flags"] for m in imap.mailbox.values())
    assert ("SELECT", '"INBOX"', True) in imap.instances[-1].calls
    rec = journal_records()[-1]
    assert rec["kind"] == "connector_pull" and rec["user"] == "petrov"
    assert "получено файлов 7" in rec["detail"]
    # повторный опрос — ничего нового
    assert c.pull(dest) == []


def test_imap_mark_seen_and_move(imap, tmp_path):
    c = connectors.ImapMailConnector("mail", imap_cfg(after_success="mark_seen", unseen_only=True))
    c.pull(str(tmp_path / "u"))
    assert imap.mailbox[11]["flags"] == {"\\Seen"} and imap.mailbox[12]["flags"] == {"\\Seen"}
    assert not imap.mailbox[13]["flags"] and not imap.mailbox[14]["flags"]
    assert "UNSEEN" in [c for c in imap.instances[-1].calls if c[0] == "SEARCH"][0]
    assert ("SELECT", '"INBOX"', False) in imap.instances[-1].calls

    FakeIMAP.mailbox = build_mailbox()
    c = connectors.ImapMailConnector("mail2", imap_cfg(after_success="move",
                                                       move_to="SibDoc/Обработано",
                                                       security="starttls"))
    got = c.pull(str(tmp_path / "u2"))
    assert len(got) == 7
    assert set(FakeIMAP.mailbox) == {13, 14}
    calls = imap.instances[-1].calls
    assert ("STARTTLS",) in calls
    assert ("MOVE", "11", '"SibDoc/&BB4EMQRABDAEMQQ+BEIEMAQ9BD4-"') in calls


def test_imap_move_requires_folder():
    c = connectors.ImapMailConnector("m", imap_cfg(after_success="move"))
    assert "move_to" in c.missing()


# ============================================================================ REST
def test_rest_next_link_bearer_records_path(http, tmp_path, monkeypatch):
    monkeypatch.setenv("SIBDOC_TEST_TOKEN", "tok-abc-123456")
    pages = {
        "https://api.test/v1/decl": {"data": {"list": [{"id": 1, "no": "报关单-1"},
                                                       {"id": 2, "no": "报关单-2"}]},
                                     "links": {"next": "/v1/decl?page=2"}},
        "https://api.test/v1/decl?page=2": {"data": {"list": [{"id": 3, "no": "x", "extra": {"a": 1}}]},
                                            "links": {"next": None}},
    }
    http.routes.append((lambda m, u, c: u in pages, lambda c: FakeResp(pages[c["url"]])))
    c = connectors.RestConnector("api", {"url": "https://api.test/v1/decl", "auth": "bearer",
                                         "token_env": "SIBDOC_TEST_TOKEN",
                                         "records_path": "data.list",
                                         "next_link_path": "links.next", "slot": "cn_dec"})
    got = c.pull(str(tmp_path))
    assert got == [{"slot": "cn_dec", "file": "cn_dec.csv", "path": str(tmp_path / "cn_dec.csv"),
                    "rows": 3}]
    raw = (tmp_path / "cn_dec.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert [r["no"] for r in rows] == ["报关单-1", "报关单-2", "x"]
    assert rows[2]["extra"] == '{"a": 1}'
    assert http.calls[0]["headers"]["Authorization"] == "Bearer tok-abc-123456"
    assert "tok-abc-123456" not in journal_text()


def test_rest_page_mode_basic_and_api_key(http, tmp_path):
    def page_resp(call):
        p = int(call["params"]["page"])
        items = [{"n": i} for i in range((p - 1) * 2, min(p * 2, 5))]
        return FakeResp({"items": items})
    http.routes.append((lambda m, u, c: u == "https://api.test/items", page_resp))
    c = connectors.RestConnector("api", {"url": "https://api.test/items", "auth": "basic",
                                         "username": "svc", "password_env": "SIBDOC_TEST_PASSWORD",
                                         "pagination": "page", "page_param": "page",
                                         "page_size_param": "size", "page_size": 2,
                                         "records_path": "items", "slot": "purchase"})
    got = c.pull(str(tmp_path))
    assert got[0]["rows"] == 5
    assert [cl["params"]["page"] for cl in http.calls] == [1, 2, 3]
    assert http.calls[0]["auth"] == ("svc", PASSWORD)

    http.calls.clear()
    http.routes.insert(0, (lambda m, u, c: u == "https://api.test/one",
                           FakeResp([{"a": 1}, {"a": 2}])))
    c = connectors.RestConnector("k", {"url": "https://api.test/one", "auth": "api_key_header",
                                       "api_key_header": "X-API-Key",
                                       "api_key_env": "SIBDOC_TEST_PASSWORD", "slot": "purchase"})
    res = c.test()
    assert res["ok"] and "записей на первой странице: 2" in res["detail"]
    assert http.calls[0]["headers"]["X-API-Key"] == PASSWORD


def test_rest_errors_are_redacted(http, tmp_path):
    http.routes.append((lambda m, u, c: True,
                        FakeResp(None, status=401, text=f"denied for {PASSWORD}")))
    c = connectors.RestConnector("api", {"url": "https://api.test/x", "auth": "basic",
                                         "username": "svc", "password_env": "SIBDOC_TEST_PASSWORD",
                                         "slot": "purchase"})
    with pytest.raises(ConnectorError) as ei:
        c.pull(str(tmp_path))
    assert "HTTP 401" in str(ei.value) and PASSWORD not in str(ei.value)
    rec = journal_records()[-1]
    assert rec["kind"] == "connector_error" and PASSWORD not in journal_text()
    assert not os.path.exists(tmp_path / "purchase.csv")


# ============================================================================ SAP OData
SAP_BASE = "https://sap.test/sap/opu/odata/sap/API_PO"


def test_sap_v2_results_next_and_dates(http, tmp_path):
    pages = {
        f"{SAP_BASE}/A_PurchaseOrderItem": {"d": {"results": [
            {"__metadata": {"uri": "x"}, "PurchaseOrder": "4500000001",
             "CreationDate": "/Date(1760918400000)/", "to_Header": {"__deferred": {"uri": "y"}}}],
            "__next": f"{SAP_BASE}/A_PurchaseOrderItem?$skiptoken=1"}},
        f"{SAP_BASE}/A_PurchaseOrderItem?$skiptoken=1": {"d": {"results": [
            {"PurchaseOrder": "4500000002", "CreationDate": "/Date(1760918400000)/"}]}},
    }
    http.routes.append((lambda m, u, c: u in pages, lambda c: FakeResp(pages[c["url"]])))
    c = connectors.SapODataConnector("sap", {"base_url": SAP_BASE, "entity": "A_PurchaseOrderItem",
                                             "params": {"$top": "50000"}, "username": "svc",
                                             "password_env": "SIBDOC_TEST_PASSWORD",
                                             "slots": ["purchase"]})
    got = c.pull(str(tmp_path))
    assert got[0]["rows"] == 2 and got[0]["slot"] == "purchase"
    rows = list(csv.DictReader(open(got[0]["path"], encoding="utf-8-sig")))
    assert list(rows[0].keys()) == ["PurchaseOrder", "CreationDate"]
    assert rows[0]["CreationDate"] == "2025-10-20"
    assert http.calls[0]["params"] == {"$top": "50000"} and http.calls[1]["params"] == {}
    assert http.calls[0]["auth"] == ("svc", PASSWORD)


def test_sap_v4_value_nextlink_oauth2(http, tmp_path, monkeypatch):
    monkeypatch.setenv("SIBDOC_TEST_CS", "client-secret-xyz")
    base4 = "https://sap.test/sap/opu/odata4/sap/api_po/srvd_a2x/sap/purchaseorder/0001"
    pages = {
        f"{base4}/PurchaseOrderItem": {"@odata.context": "$metadata#X", "value": [
            {"PurchaseOrder": "1", "@odata.etag": "W/1"}],
            "@odata.nextLink": "PurchaseOrderItem?$skiptoken=2"},
        f"{base4}/PurchaseOrderItem?$skiptoken=2": {"value": [{"PurchaseOrder": "2"}]},
    }
    http.routes.append((lambda m, u, c: u == "https://auth.test/oauth/token",
                        FakeResp({"access_token": "AT-1"})))
    http.routes.append((lambda m, u, c: u in pages, lambda c: FakeResp(pages[c["url"]])))
    http.routes.append((lambda m, u, c: u.endswith("/$metadata"),
                        FakeResp(None, text='<edmx:Edmx Version="4.0"></edmx:Edmx>')))
    cfg = {"base_url": base4, "entity": "PurchaseOrderItem", "auth": "oauth2",
           "token_url": "https://auth.test/oauth/token", "client_id": "sb-1",
           "client_secret_env": "SIBDOC_TEST_CS", "slot": "purchase"}
    c = connectors.SapODataConnector("sap4", cfg)
    got = c.pull(str(tmp_path))
    assert got[0]["rows"] == 2
    token_call = http.calls[0]
    assert token_call["method"] == "POST" and token_call["data"] == {"grant_type": "client_credentials"}
    assert http.calls[1]["headers"]["Authorization"] == "Bearer AT-1"
    rows = list(csv.DictReader(open(got[0]["path"], encoding="utf-8-sig")))
    assert [r["PurchaseOrder"] for r in rows] == ["1", "2"] and "@odata.etag" not in rows[0]
    res = c.test()
    assert res["ok"] and "OData v4" in res["detail"]


# ============================================================================ 1С / Kingdee / Yonyou
def test_onec_odata_offset_pagination(http, tmp_path):
    def resp(call):
        skip = int(call["params"].get("$skip", 0))
        top = int(call["params"]["$top"])
        data = [{"Number": f"N{i}", "Date": "2026-10-01T00:00:00"} for i in range(skip, min(skip + top, 3))]
        return FakeResp({"odata.metadata": "x", "value": data})
    http.routes.append((lambda m, u, c: "/odata/standard.odata/Document_" in u, resp))
    c = connectors.OneCODataConnector("onec", {"base_url": "https://1c.test/trade",
                                               "entity": "Document_ПоступлениеТоваровУслуг",
                                               "username": "sibdoc",
                                               "password_env": "SIBDOC_TEST_PASSWORD",
                                               "filter": "Posted eq true", "page_size": 2,
                                               "slot": "purchase"})
    assert c.missing() == []
    got = c.pull(str(tmp_path))
    assert got[0]["rows"] == 3
    first = http.calls[0]
    assert first["params"]["$format"] == "json" and first["params"]["$filter"] == "Posted eq true"
    assert first["params"]["$top"] == 2 and first["params"]["$skip"] == 0
    assert first["auth"] == ("sibdoc", PASSWORD)
    assert "%D0%9F" in first["url"]                                    # кириллица закодирована


def test_kingdee_login_and_bill_query(http, tmp_path):
    rows = [["CGDD000001", "2026-10-01", 10], ["CGDD000002", "2026-10-02", 5]]
    http.routes.append((lambda m, u, c: u.endswith("ValidateUser.common.kdsvc"),
                        lambda c: FakeResp({"LoginResultType": 1, "Message": None})))
    http.routes.append((lambda m, u, c: u.endswith("ExecuteBillQuery.common.kdsvc"),
                        lambda c: FakeResp(rows[c["json"]["data"]["StartRow"]:]
                                           [:c["json"]["data"]["Limit"]])))
    c = connectors.KingdeeConnector("kd", {"server_url": "https://erp.test/k3cloud", "acct_id": "A1",
                                           "username": "api", "password_env": "SIBDOC_TEST_PASSWORD",
                                           "form_id": "PUR_PurchaseOrder",
                                           "field_keys": "FBillNo,FDate,FQty", "page_size": 1,
                                           "slot": "purchase"})
    got = c.pull(str(tmp_path))
    assert got[0]["rows"] == 2
    login = http.calls[0]
    assert login["json"] == {"acctID": "A1", "username": "api", "password": PASSWORD, "lcid": 2052}
    out = list(csv.DictReader(open(got[0]["path"], encoding="utf-8-sig")))
    assert out[0] == {"FBillNo": "CGDD000001", "FDate": "2026-10-01", "FQty": "10"}
    assert PASSWORD not in journal_text()

    http.routes.insert(0, (lambda m, u, c: u.endswith("ExecuteBillQuery.common.kdsvc"),
                           FakeResp([[{"Result": {"ResponseStatus": {
                               "IsSuccess": False, "Errors": [{"Message": "字段不存在"}]}}}]])))
    res = c.test()
    assert res["ok"] is False and "字段不存在" in res["detail"]


def test_yonyou_token_signature_and_query(http, tmp_path, monkeypatch):
    monkeypatch.setenv("SIBDOC_TEST_YS", "app-secret-0001")
    monkeypatch.setattr("time.time", lambda: 1760000000.123)
    expected_sig = base64.b64encode(
        __import__("hmac").new(b"app-secret-0001", b"appKeyAK1timestamp1760000000123",
                               hashlib.sha256).digest()).decode()
    assert yonyou_signature("AK1", "app-secret-0001", "1760000000123") == expected_sig
    http.routes.append((lambda m, u, c: "getAccessToken" in u,
                        lambda c: FakeResp({"code": "00000", "data": {"access_token": "YT-9",
                                                                       "expire": 7200}})))
    http.routes.append((lambda m, u, c: u == "https://gw.test/iuap-api-gateway/yonbip/x/list",
                        lambda c: FakeResp({"code": "200", "data": {"recordList":
                            [{"code": "PO1"}] if c["json"]["pageIndex"] == 1 else []}})))
    c = connectors.YonyouConnector("yy", {"auth_url": "https://gw.test/iuap-api-auth",
                                          "gateway_url": "https://gw.test/iuap-api-gateway",
                                          "app_key": "AK1", "app_secret_env": "SIBDOC_TEST_YS",
                                          "api_path": "/yonbip/x/list", "page_size": 1,
                                          "slot": "purchase"})
    got = c.pull(str(tmp_path))
    assert got[0]["rows"] == 1
    tok = http.calls[0]
    assert tok["params"] == {"appKey": "AK1", "timestamp": "1760000000123",
                             "signature": expected_sig}
    q = http.calls[1]
    assert q["params"]["access_token"] == "YT-9" and q["json"]["pageSize"] == 1
    assert "YT-9" not in journal_text() and "app-secret-0001" not in journal_text()


# ============================================================================ SFTP (фейковый paramiko)
SERVER_KEY_BLOB = b"fake-ed25519-server-key"


def fake_paramiko(files: dict[str, bytes]):
    mod = types.ModuleType("paramiko")

    class SSHException(Exception):
        pass

    class BadHostKeyException(SSHException):
        pass

    class MissingHostKeyPolicy:
        def missing_host_key(self, client, hostname, key):
            pass

    class AutoAddPolicy(MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            client.known[hostname] = key.asbytes()

    class RejectPolicy(MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            raise SSHException(f"Server {hostname!r} not found in known_hosts")

    class Key:
        def asbytes(self):
            return SERVER_KEY_BLOB

    class Attr:
        def __init__(self, name, mode, size):
            self.filename, self.st_mode, self.st_size = name, mode, size

    class SFTP:
        def listdir_attr(self, path):
            out = [Attr("subdir", stat.S_IFDIR | 0o755, 0)]
            for p, data in files.items():
                d, _, n = p.rpartition("/")
                if d == path.rstrip("/"):
                    out.append(Attr(n, stat.S_IFREG | 0o644, len(data)))
            return out

        def getfo(self, remotepath, fh):
            mod.downloads.append(remotepath)
            fh.write(files[remotepath])

        def close(self):
            pass

    class SSHClient:
        def __init__(self):
            self.known: dict = {}
            self.policy = RejectPolicy()
            mod.clients.append(self)
            self.connect_kwargs = None

        def load_host_keys(self, path):
            with open(path) as fh:
                for line in fh:
                    host, _kt, b64 = line.split()
                    self.known[host] = base64.b64decode(b64)

        def set_missing_host_key_policy(self, policy):
            self.policy = policy

        def connect(self, hostname, port=22, **kw):
            self.connect_kwargs = dict(kw, hostname=hostname, port=port)
            lookup = hostname if port == 22 else f"[{hostname}]:{port}"
            if lookup in self.known:
                if self.known[lookup] != SERVER_KEY_BLOB:
                    raise BadHostKeyException("host key mismatch")
            else:
                self.policy.missing_host_key(self, lookup, Key())

        def open_sftp(self):
            return SFTP()

        def close(self):
            pass

    mod.SSHException = SSHException
    mod.BadHostKeyException = BadHostKeyException
    mod.MissingHostKeyPolicy = MissingHostKeyPolicy
    mod.AutoAddPolicy = AutoAddPolicy
    mod.RejectPolicy = RejectPolicy
    mod.SSHClient = SSHClient
    mod.clients = []
    mod.downloads = []
    return mod


@pytest.fixture
def pk(monkeypatch, tmp_path):
    mod = fake_paramiko({"/uploads/sibdoc/报关单_1022.xlsx": b"x1",
                         "/uploads/sibdoc/ДТ_10702070.xml": b"<x/>",
                         "/uploads/sibdoc/notes.txt": b"t"})
    monkeypatch.setitem(sys.modules, "paramiko", mod)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))          # нет ~/.ssh/known_hosts
    return mod


def sftp_cfg(**kw):
    cfg = {"host": "sftp.test", "username": "svc", "password_env": "SIBDOC_TEST_PASSWORD",
           "remote_dir": "/uploads/sibdoc", "pattern": ["*.xlsx", "*.xml"],
           "slot_rules": SLOT_RULES}
    cfg.update(kw)
    return cfg


def test_sftp_rejects_without_known_hosts(pk, tmp_path):
    c = connectors.SftpConnector("s", sftp_cfg())
    res = c.test()
    assert res["ok"] is False and "known_hosts" in res["detail"]
    assert all(cl.connect_kwargs is None for cl in pk.clients)     # даже не подключались
    with pytest.raises(ConnectorError):
        c.pull(str(tmp_path / "out"))


def test_sftp_unknown_host_rejected_by_known_hosts(pk, tmp_path):
    kh = tmp_path / "known_hosts"
    kh.write_text("other.test ssh-ed25519 " + base64.b64encode(b"k").decode() + "\n")
    res = connectors.SftpConnector("s", sftp_cfg(known_hosts=str(kh))).test()
    assert res["ok"] is False and "отсутствует в known_hosts" in res["detail"]


def test_sftp_known_hosts_ok_and_pull(pk, tmp_path):
    kh = tmp_path / "known_hosts"
    kh.write_text("[sftp.test]:2222 ssh-ed25519 " + base64.b64encode(SERVER_KEY_BLOB).decode() + "\n")
    c = connectors.SftpConnector("s", sftp_cfg(known_hosts=str(kh), port=2222))
    res = c.test()
    assert res["ok"] is True and "файлов по маске" in res["detail"] and "2" in res["detail"]
    got = c.pull(str(tmp_path / "out"))
    assert sorted(pk.downloads) == ["/uploads/sibdoc/ДТ_10702070.xml",
                                    "/uploads/sibdoc/报关单_1022.xlsx"]
    slots = {i["file"]: i["slot"] for i in got}
    assert slots == {"报关单_1022.xlsx": "cn_dec", "ДТ_10702070.xml": "ru_dt"}
    kw = pk.clients[-1].connect_kwargs
    assert kw["password"] == PASSWORD and kw["look_for_keys"] is False
    assert PASSWORD not in journal_text()


def test_sftp_bad_key_in_known_hosts(pk, tmp_path):
    kh = tmp_path / "known_hosts"
    kh.write_text("sftp.test ssh-ed25519 " + base64.b64encode(b"evil").decode() + "\n")
    res = connectors.SftpConnector("s", sftp_cfg(known_hosts=str(kh))).test()
    assert res["ok"] is False and "НЕ совпадает" in res["detail"]


def test_sftp_fingerprint(pk):
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(SERVER_KEY_BLOB).digest()).decode().rstrip("=")
    assert connectors.SftpConnector("s", sftp_cfg(host_key_sha256=fp)).test()["ok"] is True
    res = connectors.SftpConnector("s", sftp_cfg(host_key_sha256="SHA256:AAAAwrong")).test()
    assert res["ok"] is False and "не совпадает" in res["detail"]


def test_sftp_insecure_accept_warns(pk):
    c = connectors.SftpConnector("s", sftp_cfg(insecure_accept_new_host=True))
    res = c.test()
    assert res["ok"] is True and "ВНИМАНИЕ" in res["detail"] and res["warnings"]
    assert any("insecure_accept_new_host" in w for w in c.public_dict()["warnings"])


def test_folder_pull_with_zip(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "wide_table_2026.xlsx").write_bytes(b"x")
    (src / "broker.zip").write_bytes(_zip_with_raw_names([("报关单_3.xlsx".encode("gbk"), b"y")]))
    c = connectors.FolderConnector("f", {"path": str(src), "pattern": ["*.xlsx", "*.zip"],
                                         "slots": ["wide_table"], "slot_rules": SLOT_RULES})
    got = c.pull(str(tmp_path / "out"))
    assert {(i["file"], i["slot"]) for i in got} == {("wide_table_2026.xlsx", "wide_table"),
                                                     ("报关单_3.xlsx", "cn_dec")}
    assert os.path.basename(next(i["path"] for i in got if i["slot"] == "wide_table")) \
        == "wide_table_2026.xlsx"                                     # префикс уже есть
