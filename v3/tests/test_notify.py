# -*- coding: utf-8 -*-
"""Тесты уведомлений app/notify — без сети: requests и smtplib подменяются."""
from __future__ import annotations

import json
import os
import smtplib
import sys
import types
from urllib.parse import urlparse

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import requests  # noqa: E402

import app.journal as journal  # noqa: E402
from app import notify  # noqa: E402
from app.notify.base import split_text, utf8_len  # noqa: E402

WECOM_KEY = "wecom-key-0f1e2d3c4b5a"
TG_TOKEN = "123456789:AAH-secret_token-XYZ"
SMTP_PASS = "smtp-pass-Пароль-1"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("SIBDOC_WECOM_KEY", WECOM_KEY)
    monkeypatch.setenv("SIBDOC_TG_TOKEN", TG_TOKEN)
    monkeypatch.setenv("SIBDOC_SMTP_PASSWORD", SMTP_PASS)
    yield


def journal_text() -> str:
    if not os.path.exists(journal.JOURNAL_FILE):
        return ""
    with open(journal.JOURNAL_FILE, encoding="utf-8") as fh:
        return fh.read()


def journal_records() -> list[dict]:
    return [json.loads(x) for x in journal_text().splitlines() if x.strip()]


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._data


@pytest.fixture
def http(monkeypatch):
    state = types.SimpleNamespace(calls=[], fail=None)

    def fake(self, method, url, **kw):
        files = {}
        for name, spec in (kw.get("files") or {}).items():
            files[name] = (spec[0], spec[1].read())
        call = {"method": method.upper(), "url": url, "params": dict(kw.get("params") or {}),
                "json": kw.get("json"), "data": kw.get("data"), "files": files}
        state.calls.append(call)
        if state.fail and state.fail(call):
            raise requests.ConnectionError(f"нет связи: {url}?key={WECOM_KEY}")
        host = urlparse(url).netloc
        if host == "qyapi.weixin.qq.com":
            if url.endswith("/upload_media"):
                return FakeResp({"errcode": 0, "errmsg": "ok", "type": "file", "media_id": "MID-1"})
            if state.wecom_err:
                return FakeResp({"errcode": 93000, "errmsg": "invalid webhook url"})
            return FakeResp({"errcode": 0, "errmsg": "ok"})
        if host == "api.telegram.org":
            return FakeResp({"ok": True, "result": {}})
        raise requests.ConnectionError(f"нет связи (тест): {url}")

    state.wecom_err = False
    monkeypatch.setattr(requests.sessions.Session, "request", fake)
    return state


class FakeSMTP:
    sent: list = []
    instances: list = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port, self.log = host, port, []
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        if password != SMTP_PASS:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.log.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)

    def quit(self):
        self.log.append("quit")


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.instances = [], []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def cfg_all(**over) -> dict:
    ch = {
        "wecom_team": {"type": "wecom", "enabled": True, "key_env": "SIBDOC_WECOM_KEY",
                       "events": ["run_done", "issues_found", "connector_error"]},
        "telegram_ops": {"type": "telegram", "enabled": True, "token_env": "SIBDOC_TG_TOKEN",
                         "chat_id": "-100123", "events": ["issues_found", "connector_error"]},
        "email_team": {"type": "email", "enabled": True, "host": "smtp.test", "port": 465,
                       "security": "ssl", "username": "sibdoc@test", "password_env":
                       "SIBDOC_SMTP_PASSWORD", "from": "SibDoc <sibdoc@test>",
                       "to": ["team@test", "boss@test"], "events": ["run_done", "issues_found"]},
        "disabled_wecom": {"type": "wecom", "enabled": False, "key_env": "SIBDOC_WECOM_KEY"},
    }
    for k, v in over.items():
        ch[k].update(v)
    return {"notifications": ch}


@pytest.fixture
def report(tmp_path):
    p = tmp_path / "отчёт_报告.xlsx"
    p.write_bytes(b"PK\x03\x04" + b"0" * 100)
    return str(p)


def test_send_all_channels_with_file(http, smtp, report):
    res = notify.send(cfg_all(), "issues_found", "Найдено 5 расхождений\nCD 2226…: цена",
                      files=[report], user="petrov")
    by = {r["channel"]: r for r in res}
    assert set(by) == {"wecom_team", "telegram_ops", "email_team"}     # выключенный — пропущен
    assert all(r["ok"] for r in res), res
    # WeCom: markdown -> upload_media -> file
    wc = [c for c in http.calls if "qyapi" in c["url"]]
    assert wc[0]["url"].endswith("/send") and wc[0]["params"] == {"key": WECOM_KEY}
    assert wc[0]["json"]["msgtype"] == "markdown"
    assert wc[1]["url"].endswith("/upload_media") and wc[1]["params"]["type"] == "file"
    assert wc[1]["files"]["media"][0] == "отчёт_报告.xlsx"
    assert wc[2]["json"] == {"msgtype": "file", "file": {"media_id": "MID-1"}}
    assert by["wecom_team"]["files"] == ["отчёт_报告.xlsx"]
    # Telegram: sendMessage + sendDocument
    tg = [c for c in http.calls if "telegram" in c["url"]]
    assert tg[0]["url"].endswith("/sendMessage") and tg[0]["json"]["chat_id"] == "-100123"
    assert tg[1]["url"].endswith("/sendDocument") and tg[1]["files"]["document"][0] == "отчёт_报告.xlsx"
    # почта: SSL, логин, вложение с нелатинским именем
    msg = smtp.sent[0]
    assert ("login", "sibdoc@test") in smtp.instances[0].log
    assert msg["To"] == "team@test, boss@test" and "найдены расхождения" in msg["Subject"]
    att = [p for p in msg.iter_attachments()]
    assert att[0].get_filename() == "отчёт_报告.xlsx"
    # журнал: по записи на канал, без секретов
    recs = journal_records()
    assert [r["kind"] for r in recs] == ["notify"] * 3 and all(r["user"] == "petrov" for r in recs)
    text = journal_text() + json.dumps(res, ensure_ascii=False)
    for s in (WECOM_KEY, TG_TOKEN, SMTP_PASS):
        assert s not in text


def test_events_filter(http, smtp):
    res = notify.send(cfg_all(), "run_done", "Прогон завершён")
    assert {r["channel"] for r in res} == {"wecom_team", "email_team"}
    res = notify.send(cfg_all(), "connector_error", "imap: нет связи")
    assert {r["channel"] for r in res} == {"wecom_team", "telegram_ops"}
    assert notify.send({}, "run_done", "x") == []


def test_errors_do_not_raise_and_are_redacted(http, smtp):
    http.wecom_err = True
    http.fail = lambda call: "telegram" in call["url"]
    res = notify.send(cfg_all(email_team={"password_env": "NOPE"}), "issues_found", "текст")
    by = {r["channel"]: r for r in res}
    assert by["wecom_team"]["ok"] is False and "93000" in by["wecom_team"]["detail"]
    assert by["telegram_ops"]["ok"] is False and "ConnectionError" in by["telegram_ops"]["detail"]
    assert by["email_team"]["ok"] is False
    text = journal_text() + json.dumps(res, ensure_ascii=False)
    for s in (WECOM_KEY, TG_TOKEN, SMTP_PASS):
        assert s not in text
    assert all(r["kind"] == "notify" for r in journal_records())


def test_missing_secret_reported(http, monkeypatch):
    monkeypatch.delenv("SIBDOC_WECOM_KEY")
    res = notify.send(cfg_all(), "run_done", "x")
    by = {r["channel"]: r for r in res}
    assert by["wecom_team"]["ok"] is False and "key_env" in by["wecom_team"]["detail"]
    assert not [c for c in http.calls if "qyapi" in c["url"]]


def test_wecom_markdown_split_by_bytes(http):
    long = "\n".join(f"строка {i}: 报关单 2226202610001{i:05d} — цена не совпадает" for i in range(300))
    res = notify.send(cfg_all(), "run_done", long)
    wc = [c for c in http.calls if "qyapi" in c["url"]]
    assert 1 < len(wc) <= 3
    for c in wc:
        assert len(c["json"]["markdown"]["content"].encode("utf-8")) <= 4096
    assert wc[-1]["json"]["markdown"]["content"].endswith("(сообщение обрезано)")
    assert next(r for r in res if r["channel"] == "wecom_team")["parts"] == len(wc)


def test_split_text_helper():
    assert split_text("abc", 10) == ["abc"]
    parts = split_text("x" * 25, 10, max_parts=5)
    assert parts == ["x" * 10, "x" * 10, "x" * 5]
    parts = split_text("报" * 3000, 4096, unit=utf8_len, max_parts=2)
    assert len(parts) == 2 and all(len(p.encode()) <= 4096 for p in parts)


def test_test_channel_disabled_and_unknown(http, smtp):
    res = notify.test_channel(cfg_all(), "disabled_wecom", user="admin")
    assert res["ok"] is True
    assert "тестовое сообщение" in http.calls[-1]["json"]["markdown"]["content"]
    assert journal_records()[-1]["meta"]["event"] == "test"
    assert notify.test_channel(cfg_all(), "nope")["ok"] is False


def test_email_starttls(smtp):
    cfg = cfg_all(email_team={"security": "starttls", "port": 587})
    res = notify.test_channel(cfg, "email_team")
    assert res["ok"] is True
    assert smtp.instances[-1].port == 587 and "starttls" in smtp.instances[-1].log


def test_public_dict_hides_secrets():
    cfg = cfg_all(telegram_ops={"token": "literal-token-123456", "token_env": None})
    chans = {c.id: c.public_dict() for c in notify.list_channels(cfg)}
    dumped = json.dumps(chans, ensure_ascii=False)
    assert WECOM_KEY not in dumped and "literal-token-123456" not in dumped
    tg = {p["key"]: p for p in chans["telegram_ops"]["params"]}
    assert tg["token"]["set"] is True and tg["token"]["source"] == "literal"
    assert chans["telegram_ops"]["warnings"]
    assert {c["type"] for c in notify.catalog()} == {"wecom", "telegram", "email"}
