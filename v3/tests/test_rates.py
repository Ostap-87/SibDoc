# -*- coding: utf-8 -*-
"""Тесты модуля курсов app/rates — без сети.

Ответы ЦБ РФ (XML, windows-1251) и CFETS (JSON) — реальные публичные курсы,
сохранённые в tests/fixtures/rates/ (урезаны до нескольких валют).
HTTP подменяется через monkeypatch: любой непредусмотренный запрос = «нет связи».
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
from urllib.parse import urlencode

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app.journal as journal  # noqa: E402
from app import rates  # noqa: E402
from app.rates import providers, storage  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "rates")
TODAY = dt.date(2026, 10, 22)


def _fix(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


class FakeHTTP:
    """Подмена providers.http_request: отдаёт образцы ответов, считает запросы."""

    CBR = {"15/10/2026": "cbr_2026-10-15.xml", "17/10/2026": "cbr_2026-10-18.xml",
           "18/10/2026": "cbr_2026-10-18.xml", "26/10/2026": "cbr_2026-10-15.xml",
           "01/01/2030": "cbr_empty.xml"}

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.down = False            # True — сеть недоступна
        self.cfets_history_fails = False

    def __call__(self, method, url, *, params=None, timeout=15.0):
        params = dict(params or {})
        self.calls.append((method, url, params))
        if self.down:
            raise providers.SourceDown("www.cbr.ru: нет связи (ConnectionError)")
        if url == providers.CBR_URL:
            name = self.CBR.get(params.get("date_req"))
            if not name:
                raise providers.SourceDown("нет образца для " + urlencode(params))
            return _fix(name)
        if url == providers.CFETS_HISTORY_URL:
            if self.cfets_history_fails:
                raise providers.RateUnavailable("www.chinamoney.com.cn ответил HTTP 500")
            payload = json.loads(_fix("cfets_history_2026-09-24_2026-10-20.json"))
            payload["records"] = [r for r in payload["records"]
                                  if params["startDate"] <= r["date"] <= params["endDate"]]
            return json.dumps(payload).encode("utf-8")
        if url == providers.CFETS_CURRENT_URL:
            return _fix("cfets_ccpr_2026-10-22.json")
        raise providers.SourceDown("неожиданный адрес " + url)

    def count(self, url=None) -> int:
        return sum(1 for c in self.calls if url is None or c[1] == url)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Отдельное хранилище, журнал и настройки на каждый тест; «сегодня» = 22.10.2026."""
    monkeypatch.setattr(storage, "RATES_DIR", str(tmp_path / "rates"))
    monkeypatch.setattr(storage, "CONFIG_FILE", str(tmp_path / "rates.yaml"))
    monkeypatch.setattr(journal, "JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(providers, "today", lambda: TODAY)
    storage._MEMO.clear()
    rates._reset_memory()
    yield tmp_path
    storage._MEMO.clear()
    rates._reset_memory()


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(providers, "http_request", fake)
    return fake


def journal_records() -> list[dict]:
    if not os.path.exists(journal.JOURNAL_FILE):
        return []
    with open(journal.JOURNAL_FILE, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def kinds() -> list[str]:
    return [r["kind"] for r in journal_records()]


# ===================================================================== валюты и даты
@pytest.mark.parametrize("raw, code", [
    (156, "CNY"), ("156", "CNY"), (156.0, "CNY"), ("156.0", "CNY"), ("840", "USD"),
    ("643", "RUB"), ("810", "RUB"), ("978", "EUR"), ("398", "KZT"), ("036", "AUD"),
    ("人民币", "CNY"), ("美元", "USD"), ("欧元", "EUR"), ("卢布", "RUB"),
    ("俄罗斯卢布", "RUB"), ("白俄罗斯卢布", "BYN"), ("港币", "HKD"), ("日元", "JPY"),
    (" usd ", "USD"), ("ｕｓｄ", "USD"), ("руб.", "RUB"), ("RUR", "RUB"), ("RMB", "CNY"),
    ("долл. США", "USD"), ("евро", "EUR"), ("USD (доллар США)", "USD"),
    ("502", "USD"), ("142", "CNY"), ("300", "EUR"), ("344", "HKD"),
    ("人民币(CNY)", "CNY"), ("美元（USD）", "USD"), ("欧元 (欧盟)", "EUR"), ("CNY-人民币", "CNY"),
    (None, ""), (float("nan"), ""), ("", ""), ("XYZ", "XYZ"),
])
def test_norm_currency(raw, code):
    assert rates.norm_currency(raw) == code


@pytest.mark.parametrize("raw", ["2026-10-15", "15.10.2026", "20261015", "2026/10/15",
                                 "2026-10-15 00:00:00", "2026年10月15日",
                                 dt.date(2026, 10, 15), dt.datetime(2026, 10, 15, 12, 30)])
def test_date_formats(http, raw):
    assert rates.get_rate("USD", "RUB", raw).date == "2026-10-15"


def test_date_pandas_timestamp(http):
    pd = pytest.importorskip("pandas")
    assert rates.get_rate("USD", "RUB", pd.Timestamp("2026-10-15")).value == 83.9497


def test_bad_inputs_raise_rate_unavailable_and_value_error(http):
    with pytest.raises(rates.RateUnavailable):
        rates.get_rate("ZZZZ", "RUB", "2026-10-15")
    with pytest.raises(ValueError):
        rates.get_rate("USD", "RUB", "вчера")
    with pytest.raises(ValueError):
        rates.get_rate("USD", "RUB", "2026-10-15", side="mars")
    assert http.count() == 0


# ===================================================================== ЦБ РФ
def test_parse_cbr_nominal():
    eff, values, quoted = providers.parse_cbr_xml(_fix("cbr_2026-10-18.xml"))
    assert eff == "2026-10-17"                         # атрибут Date корня
    assert values["USD"] == pytest.approx(83.6177)
    assert values["CNY"] == pytest.approx(12.4950)
    assert values["JPY"] == pytest.approx(52.8691 / 100)     # Nominal = 100
    assert values["KZT"] == pytest.approx(18.4123 / 100)
    assert values["KRW"] == pytest.approx(62.0079 / 1000)    # Nominal = 1000
    assert quoted["JPY"] == "100 JPY = 52.8691 RUB"
    assert "USD" not in quoted


def test_cbr_weekday(http):
    info = rates.get_rate("USD", "RUB", "2026-10-15")
    assert info.value == 83.9497
    assert (info.date, info.effective_date) == ("2026-10-15", "2026-10-15")
    assert (info.source, info.manual, info.note) == ("cbr", False, "")
    d = info.as_dict()
    assert d["text"] == "1 USD = 83.9497 RUB" and d["source"] == "cbr"


def test_cbr_weekend_effective_date_and_cache(http):
    info = rates.get_rate("CNY", "RUB", "2026-10-18")          # воскресенье
    assert info.value == 12.495
    assert info.effective_date == "2026-10-17"
    assert "2026-10-17" in info.note
    cache = storage.cache_load()["cbr"]
    assert cache["2026-10-18"]["effective_date"] == "2026-10-17"
    assert cache["2026-10-17"]["CNY"] == pytest.approx(12.495)
    calls = http.count()
    rates.get_rate("USD", "RUB", "2026-10-18")
    rates.get_rate("EUR", "RUB", "2026-10-17")                 # тоже из кэша
    assert http.count() == calls


def test_cbr_nominal_in_get_rate(http):
    assert rates.get_rate("JPY", "RUB", "2026-10-18").value == pytest.approx(0.528691)
    assert rates.get_rate("398", "643", "2026-10-18").value == pytest.approx(0.184123)
    assert rates.get_rate("RUB", "JPY", "2026-10-18").value == pytest.approx(100 / 52.8691)


def test_cbr_cross_rate(http):
    info = rates.get_rate("USD", "EUR", "2026-10-15")
    assert info.value == pytest.approx(83.9497 / 93.9229)
    assert "кросс-курс через RUB" in info.note
    assert info.source == "cbr" and not info.manual


def test_cbr_empty_answer(http):
    with pytest.raises(rates.RateUnavailable, match="не вернул курсов"):
        rates.get_rate("USD", "RUB", "2030-01-01")


def test_future_date_is_provisional_and_not_cached(http):
    info = rates.get_rate("USD", "RUB", "2026-10-26")     # «сегодня» 22.10
    assert info.effective_date == "2026-10-15"
    assert "ещё не опубликован" in info.note
    assert "2026-10-26" not in storage.cache_load()["cbr"]
    assert "2026-10-15" in storage.cache_load()["cbr"]


def test_max_back_days_limits_old_rate(http):
    rates.save_settings({"max_back_days": 2})
    with pytest.raises(rates.RateUnavailable, match="max_back_days"):
        rates.get_rate("USD", "RUB", "2026-10-26")        # последний курс 15.10 — 11 дней


# ===================================================================== CFETS
def test_cfets_pair_conversion():
    assert providers.cfets_pair_to_cny("USD/CNY", 6.7253) == ("USD", 6.7253)
    code, v = providers.cfets_pair_to_cny("100JPY/CNY", 4.2472)
    assert code == "JPY" and v == pytest.approx(0.042472)
    code, v = providers.cfets_pair_to_cny("CNY/RUB", 12.4869)
    assert code == "RUB" and v == pytest.approx(1 / 12.4869)
    assert providers.cfets_pair_to_cny("USD/EUR", 1.1) is None


def test_cfets_parse_uses_searchlist():
    payload = {"head": {"rep_code": "200"},
               "data": {"head": ["USD/CNY", "EUR/CNY", "CNY/RUB"],
                        "searchlist": ["USD/CNY", "CNY/RUB"]},
               "records": [{"date": "2026-10-16", "values": ["6.7220", "12.5152"]},
                           {"date": "2026-10-15", "values": ["6.7253", "---"]}]}
    days = providers.parse_cfets_history(payload)
    assert days["2026-10-16"][0]["RUB"] == pytest.approx(1 / 12.5152)
    assert "EUR" not in days["2026-10-16"][0]
    assert days["2026-10-15"][0] == {"USD": 6.7253}        # «---» пропущено


def test_cfets_inverted_and_100jpy(http):
    usd = rates.get_rate("USD", "CNY", "2026-10-15", side="cn")
    assert usd.value == 6.7253 and usd.source == "cfets" and usd.effective_date == "2026-10-15"
    assert rates.get_rate("CNY", "RUB", "2026-10-15", side="cn").value == pytest.approx(12.4869, abs=1e-9)
    assert rates.get_rate("RUB", "CNY", "2026-10-15", side="cn").value == pytest.approx(1 / 12.4869)
    assert rates.get_rate("JPY", "CNY", "2026-10-15", side="cn").value == pytest.approx(0.042472)
    assert rates.get_rate("KRW", "CNY", "2026-10-15", side="cn").value == pytest.approx(1 / 200.42)
    # одно окно истории закэшировано целиком — следующий день без запроса
    calls = http.count()
    assert rates.get_rate("USD", "CNY", "2026-10-14", side="cn").value == 6.7249
    assert http.count() == calls


def test_cfets_holiday_takes_last_published(http):
    info = rates.get_rate("USD", "CNY", "2026-10-05", side="cn")   # 1–7 октября — праздник
    assert info.effective_date == "2026-09-30"
    assert info.value == pytest.approx(6.7458, abs=0.05)
    rec = storage.cache_load()["cfets"]["2026-09-30"]
    assert rec["effective_date"] == "2026-09-30"
    rates.save_settings({"max_back_days": 3})
    with pytest.raises(rates.RateUnavailable):
        rates.get_rate("USD", "CNY", "2026-10-06", side="cn")


def test_cfets_cross_rate(http):
    info = rates.get_rate("EUR", "USD", "2026-10-15", side="cn")
    assert info.value == pytest.approx(7.5068 / 6.7253)
    assert "кросс-курс через CNY" in info.note


def test_cfets_has_no_kzt(http):
    with pytest.raises(rates.RateUnavailable, match="KZT"):
        rates.get_rate("KZT", "CNY", "2026-10-15", side="cn")


def test_cfets_current_day_fallback(http):
    http.cfets_history_fails = True
    info = rates.get_rate("USD", "CNY", "2026-10-22", side="cn")       # сегодня
    assert info.value == 6.7195 and info.effective_date == "2026-10-22"
    assert http.count(providers.CFETS_CURRENT_URL) == 1


# ===================================================================== общие правила
def test_same_currency_without_network(http):
    info = rates.get_rate("CNY", "人民币", "2026-10-15", side="cn")
    assert (info.value, info.manual) == (1.0, False)
    total, info = rates.convert(250, "840", "USD", "2026-10-15")
    assert total == 250 and info.value == 1.0
    assert http.count() == 0


def test_convert(http):
    total, info = rates.convert(1000, "USD", "RUB", "2026-10-15")
    assert total == pytest.approx(83949.7)
    assert info.source == "cbr"
    total, _ = rates.convert("1 000,50", "美元", "卢布", "2026-10-15")
    assert total == pytest.approx(1000.5 * 83.9497)
    total, info = rates.convert(1000, "USD", "CNY", "2026-10-15", side="cn")
    assert total == pytest.approx(6725.3) and info.source == "cfets"


def test_explicit_source_beats_side(http):
    info = rates.get_rate("USD", "CNY", "2026-10-15", side="ru", source="cfets")
    assert info.source == "cfets" and info.value == 6.7253


# ===================================================================== режимы и ручные курсы
def test_official_mode_prefers_official(http):
    rates.set_manual("USD", "RUB", 100, date="2026-10-15")
    info = rates.get_rate("USD", "RUB", "2026-10-15")
    assert info.value == 83.9497 and not info.manual and info.source == "cbr"


def test_fallback_to_manual_when_official_unavailable(http):
    http.down = True
    with pytest.raises(rates.RateUnavailable, match="нет связи"):
        rates.get_rate("USD", "RUB", "2026-10-20")
    rates.set_manual("USD", "RUB", "90,5", date="2026-10-20", comment="курс из ДТ")
    info = rates.get_rate("USD", "RUB", "2026-10-20")
    assert info.value == 90.5 and info.manual and info.source == "manual"
    assert info.note.startswith("ручной: официальный недоступен")
    assert info.effective_date == "2026-10-20"
    # обратный курс и запись «на более раннюю дату» в пределах max_back_days
    assert rates.get_rate("RUB", "USD", "2026-10-21").value == pytest.approx(1 / 90.5)
    fails = [r for r in journal_records() if r["kind"] == "rate_fetch"]
    assert fails and fails[0]["meta"]["ok"] is False


def test_fallback_mixed_leg_when_currency_missing(http):
    # в CFETS нет тенге: ручной KZT/CNY + официальный USD/CNY
    rates.set_manual("CNY", "KZT", 71.5, month="2026-10", source_scope="cfets")
    info = rates.get_rate("USD", "KZT", "2026-10-15", side="cn")
    assert info.value == pytest.approx(6.7253 * 71.5)
    assert info.manual and info.source == "manual"
    assert "официальный недоступен" in info.note and "кросс-курс через CNY" in info.note


def test_source_down_is_not_retried_immediately(http):
    http.down = True
    with pytest.raises(rates.RateUnavailable):
        rates.get_rate("USD", "RUB", "2026-10-20")
    with pytest.raises(rates.RateUnavailable):
        rates.get_rate("USD", "RUB", "2026-10-19")
    assert http.count() == 1


def test_manual_override_mode(http):
    rates.set_manual("USD", "RUB", 100, date="2026-10-15", source_scope="cbr")
    rates.set_manual("EUR", "RUB", 105, date="2026-10-14")       # другая дата
    saved = rates.save_settings({"mode": "manual_override"}, user="anna")
    assert saved["mode"] == "manual_override"
    info = rates.get_rate("USD", "RUB", "2026-10-15")
    assert info.value == 100 and info.source == "manual" and info.manual
    assert "manual_override" in info.note
    # запись на более раннюю дату не перебивает официальный курс на дату
    eur = rates.get_rate("EUR", "RUB", "2026-10-15")
    assert eur.value == 93.9229 and eur.source == "cbr"
    # кросс: ручная нога USD + официальная EUR
    cross = rates.get_rate("USD", "EUR", "2026-10-15")
    assert cross.value == pytest.approx(100 / 93.9229) and cross.manual
    assert "кросс-курс через RUB" in cross.note
    # запись для cfets не действует на ЦБ РФ
    rates.set_manual("GBP", "RUB", 1, date="2026-10-15", source_scope="cfets")
    assert rates.get_rate("GBP", "RUB", "2026-10-15").value == 111.225


def test_manual_month_entry_in_override(http):
    rates.save_settings({"mode": "manual_override"})
    rates.set_manual("USD", "CNY", 7.0, month="2026-10")
    info = rates.get_rate("USD", "CNY", "2026-10-15", side="cn")
    assert info.value == 7.0 and info.effective_date == "2026-10-01"
    # запись на месяц действует только в своём месяце
    nov = rates.get_rate("USD", "CNY", "2026-11-02", side="cn")
    assert nov.source == "cfets" and "ещё не опубликован" in nov.note


def test_china_customs_month_table(http):
    rates.set_manual("USD", "CNY", 7.1, month="2026-10", source_scope="china_customs")
    rates.set_manual("CNY", "RUB", 11.0, month="2026-10", source_scope="china_customs")
    info = rates.get_rate("USD", "CNY", "2026-10-15", side="cn_customs")
    assert info.value == 7.1 and info.source == "china_customs" and info.manual
    assert info.effective_date == "2026-10-01" and "таможни КНР" in info.note
    cross = rates.get_rate("USD", "RUB", "2026-10-31", side="cn_customs")
    assert cross.value == pytest.approx(7.1 * 11.0) and "кросс-курс через CNY" in cross.note
    with pytest.raises(rates.RateUnavailable, match="2026-11"):
        rates.get_rate("USD", "CNY", "2026-11-02", side="cn_customs")
    assert rates.refresh("china_customs", "2026-10-15") == {"RUB": pytest.approx(1 / 11), "USD": 7.1}
    # вся китайская сторона по курсу таможни
    rates.save_settings({"sides": {"cn": "china_customs"}})
    assert rates.get_rate("USD", "CNY", "2026-10-15", side="cn").source == "china_customs"
    assert http.count() == 0


def test_china_customs_falls_back_to_any_manual(http):
    rates.set_manual("USD", "CNY", 7.2, date="2026-11-02")
    info = rates.get_rate("USD", "CNY", "2026-11-02", side="cn_customs")
    assert info.value == 7.2 and info.source == "manual"
    assert "курс таможни КНР на 2026-11 не задан" in info.note


def test_set_manual_replace_list_delete(http):
    a = rates.set_manual("usd", "人民币", 7.1, month="2026.10", source_scope="china_customs",
                         comment="海关 октябрь", user="ivan")
    assert (a["base"], a["quote"], a["month"], a["date"]) == ("USD", "CNY", "2026-10", None)
    b = rates.set_manual("USD", "CNY", 7.15, month="2026-10", source_scope="china_customs",
                         user="ivan")
    assert b["id"] == a["id"] and len(rates.list_manual()) == 1
    rates.set_manual("USD", "RUB", 90, date="15.10.2026")
    listed = rates.list_manual()
    assert [e["date"] or e["month"] for e in listed] == ["2026-10-15", "2026-10"]
    assert rates.delete_manual(a["id"], user="ivan") is True
    assert rates.delete_manual(a["id"]) is False
    assert len(rates.list_manual()) == 1
    assert kinds().count("rate_manual_add") == 3 and kinds().count("rate_manual_del") == 1
    rec = [r for r in journal_records() if r["kind"] == "rate_manual_add"][1]
    assert rec["user"] == "ivan" and "заменён прежний 7.1" in rec["detail"]


@pytest.mark.parametrize("kwargs", [
    {"date": "2026-10-15", "month": "2026-10"}, {}, {"date": "2026-10-15", "value": 0},
    {"date": "2026-10-15", "source_scope": "bank"}, {"date": "2026-10-15", "quote": "USD"},
    {"date": "2026-10-15", "value": "abc"}, {"month": "2026-13"},
])
def test_set_manual_validation(kwargs):
    args = {"base": "USD", "quote": "RUB", "value": 90}
    args.update(kwargs)
    with pytest.raises(ValueError):
        rates.set_manual(args.pop("base"), args.pop("quote"), args.pop("value"), **args)


# ===================================================================== настройки, офлайн, обновление
def test_settings_roundtrip_keeps_comments(env):
    s = rates.load_settings()
    assert s["mode"] == "official" and s["sides"]["ru"] == "cbr" and s["offline"] is False
    saved = rates.save_settings({"mode": "manual_override", "max_back_days": "7",
                                 "sides": {"cn": "china_customs"}, "offline": True}, user="admin")
    assert saved["max_back_days"] == 7 and saved["sides"]["ru"] == "cbr"
    with open(storage.CONFIG_FILE, encoding="utf-8") as fh:
        text = fh.read()
    assert "# Режим выбора курса" in text and "mode: manual_override" in text
    again = rates.load_settings()
    assert again == saved
    with pytest.raises(ValueError):
        rates.save_settings({"mode": "auto"})
    with pytest.raises(ValueError):
        rates.save_settings({"sides": {"ru": "ecb"}})
    rec = [r for r in journal_records() if r["kind"] == "rate_settings"]
    assert len(rec) == 1 and rec[0]["user"] == "admin"
    assert rec[0]["meta"]["changes"]["mode"] == ["official", "manual_override"]
    assert "sides.cn" in rec[0]["meta"]["changes"]


def test_project_config_file_is_valid():
    path = os.path.join(ROOT, "config", "rates.yaml")
    import yaml
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert storage.validate_settings(raw) == storage.validate_settings({})


def test_offline_uses_cache_and_manual_only(http):
    rates.get_rate("USD", "RUB", "2026-10-15")
    calls = http.count()
    rates.save_settings({"offline": True})
    assert rates.get_rate("EUR", "RUB", "2026-10-15").value == 93.9229
    with pytest.raises(rates.RateUnavailable, match="офлайн"):
        rates.get_rate("USD", "RUB", "2026-10-18")
    rates.set_manual("USD", "RUB", 84, date="2026-10-18")
    assert rates.get_rate("USD", "RUB", "2026-10-18").note.startswith("ручной: официальный недоступен")
    with pytest.raises(rates.RateUnavailable):
        rates.refresh("cbr", "2026-10-18")
    assert http.count() == calls


def test_refresh_forces_download_and_logs(http):
    rates.get_rate("USD", "RUB", "2026-10-15")
    got = rates.refresh("cbr", "2026-10-15", user="olga")
    assert got["USD"] == 83.9497 and got["JPY"] == pytest.approx(0.529852)
    assert http.count(providers.CBR_URL) == 2
    fetches = [r for r in journal_records() if r["kind"] == "rate_fetch"]
    assert len(fetches) == 2 and fetches[-1]["user"] == "olga" and fetches[-1]["meta"]["forced"]
    assert "ЦБ РФ" in fetches[-1]["detail"]
    with pytest.raises(ValueError):
        rates.refresh("manual", "2026-10-15")


def test_list_rates(http):
    rates.set_manual("USD", "RUB", 90, date="2026-10-15", comment="из ДТ")
    rates.set_manual("USD", "CNY", 7.1, month="2026-10", source_scope="china_customs")
    rows = rates.list_rates("2026-10-15")
    by = {(r["source"], r["base"], r["quote"]): r for r in rows}
    assert by[("cbr", "USD", "RUB")]["value"] == 83.9497
    assert "котировка источника: 100 JPY" in by[("cbr", "JPY", "RUB")]["note"]
    rub = by[("cfets", "RUB", "CNY")]
    assert rub["value"] == pytest.approx(1 / 12.4869)
    assert "CNY/RUB = 12.4869" in rub["note"]
    man = by[("manual", "USD", "RUB")]
    assert man["manual"] and man["id"] and man["comment"] == "из ДТ"
    assert by[("china_customs", "USD", "CNY")]["value"] == 7.1
    assert {r["source"] for r in rates.list_rates("2026-10-15", source="cbr")} == {"cbr"}
    http.down = True
    rows = rates.list_rates("2026-10-16", source="cbr")
    assert len(rows) == 1 and rows[0]["value"] is None and rows[0]["note"].startswith("недоступно")


def test_journal_records_fetch(http):
    rates.get_rate("USD", "RUB", "2026-10-18")
    rec = [r for r in journal_records() if r["kind"] == "rate_fetch"]
    assert len(rec) == 1
    assert rec[0]["meta"]["effective_date"] == "2026-10-17" and rec[0]["meta"]["ok"] is True
    assert "фактическая дата курса 2026-10-17" in rec[0]["detail"]


def test_storage_is_atomic_json(http):
    rates.set_manual("USD", "RUB", 90, date="2026-10-15")
    rates.get_rate("USD", "RUB", "2026-10-15")
    folder = storage.RATES_DIR
    assert sorted(os.listdir(folder)) == ["cache.json", "manual.json"]   # без .tmp-хвостов
    with open(storage.manual_file(), encoding="utf-8") as fh:
        entry = json.load(fh)["entries"][0]
    for key in ("id", "source_scope", "base", "quote", "date", "month", "value", "comment",
                "user", "ts"):
        assert key in entry
    assert not math.isnan(entry["value"])
