# -*- coding: utf-8 -*-
"""Источники курсов валют.

cbr           — ЦБ РФ, официальный курс рубля. XML_daily.asp?date_req=ДД/ММ/ГГГГ.
                Ответ в windows-1251; Valute: CharCode, Nominal, Value (с запятой).
                Атрибут Date корня — дата, на которую курс реально установлен:
                на выходные и праздники ЦБ отдаёт последний установленный курс.
                Значение = рублей за 1 единицу валюты (Value / Nominal).
cfets         — центральный курс юаня (人民币汇率中间价) Народного банка Китая,
                публикует CFETS на chinamoney.com.cn в 9:15 по Пекину в рабочие дни КНР.
                История: POST /ags/ms/cm-u-bk-ccpr/CcprHisNew?startDate=&endDate=
                (JSON: data.searchlist — пары, records[].date/values — значения),
                текущий день: /r/cms/www/chinamoney/data/fx/ccpr.json.
                Значение = юаней за 1 единицу валюты. Пары «наоборот» (CNY/RUB, CNY/KRW —
                сколько валюты за 1 юань) переворачиваются, «100JPY/CNY» делится на 100.
                Тенге (KZT), сум, сом в центральном курсе нет — для них нужен ручной курс
                или кросс через ЦБ РФ.
china_customs — месячный курс таможни КНР для исчисления пошлин (海关计征汇率).
                Надёжного машинного API нет (таможня публикует таблицу на сайте
                customs.gov.cn раз в месяц), поэтому значения берутся из ручной таблицы:
                ручные записи с source_scope='china_customs' и month='ГГГГ-ММ'.
manual        — ручные курсы пользователя (storage/rates/manual.json).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

SOURCES = ("cbr", "cfets", "china_customs", "manual")

SOURCE_INFO = {
    "cbr": {
        "title": "ЦБ РФ", "pivot": "RUB", "online": True,
        "description": "Официальный курс ЦБ РФ (рублей за 1 единицу валюты), cbr.ru XML_daily.",
    },
    "cfets": {
        "title": "ЦБ КНР / CFETS (中间价)", "pivot": "CNY", "online": True,
        "description": "Центральный курс юаня НБК, публикует CFETS на chinamoney.com.cn "
                       "(юаней за 1 единицу валюты).",
    },
    "china_customs": {
        "title": "Таможня КНР (海关计征汇率)", "pivot": "CNY", "online": False,
        "description": "Месячный курс таможни КНР. Надёжного API нет: значения берутся "
                       "из ручной таблицы по месяцу (ГГГГ-ММ), source_scope = china_customs.",
    },
    "manual": {
        "title": "Ручной курс", "pivot": "", "online": False,
        "description": "Курс, заданный пользователем вручную.",
    },
}

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
CFETS_HISTORY_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
CFETS_CURRENT_URL = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/ccpr.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SibDoc-PostControl/1.0; +rates)",
    "Accept": "application/json, text/xml, */*",
}


class RateUnavailable(Exception):
    """Курса нет: источник недоступен, нет валюты, нет записи на дату и т. п."""


class SourceDown(RateUnavailable):
    """Нет связи с сайтом источника (таймаут, обрыв, DNS) — не повторять сразу."""


class RateInputError(RateUnavailable, ValueError):
    """Непонятная валюта или дата во входных данных (ловится и как ValueError)."""


@dataclass
class FetchResult:
    """Ответ источника на одну запрошенную дату."""
    source: str
    requested: str                         # запрошенная дата YYYY-MM-DD
    effective_date: str                    # фактическая дата курса
    rates: dict[str, float]                # валюта -> значение в опорной валюте
    quoted: dict[str, str] = field(default_factory=dict)   # исходная котировка, если не «1 X = …»
    url: str = ""
    other_days: dict[str, dict] = field(default_factory=dict)  # прочие даты из того же ответа


def today() -> dt.date:
    """Сегодняшняя дата (отдельной функцией — чтобы тесты могли её подменить)."""
    return dt.date.today()


# --------------------------------------------------------------------- HTTP
def http_request(method: str, url: str, *, params: dict | None = None,
                 timeout: float = 15.0) -> bytes:
    """Один HTTP-запрос через requests. Прокси и CA берутся из окружения."""
    try:
        import requests
    except ImportError as exc:                 # pragma: no cover - зависит от окружения
        raise SourceDown("нет пакета requests (pip install requests)") from exc
    host = urlparse(url).netloc
    last_exc: Exception | None = None
    for attempt in range(2):                   # один повтор при обрыве соединения
        try:
            resp = requests.request(
                method, url, params=params, timeout=timeout, headers=HEADERS,
                verify=os.environ.get("REQUESTS_CA_BUNDLE", True))
        except requests.Timeout as exc:
            raise SourceDown(f"{host}: нет ответа за {timeout:g} с") from exc
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise SourceDown(f"{host}: нет связи ({exc.__class__.__name__})") from exc
        if resp.status_code != 200:
            raise RateUnavailable(f"{host} ответил HTTP {resp.status_code}")
        return resp.content
    raise SourceDown(f"{host}: нет связи ({last_exc.__class__.__name__})")  # pragma: no cover


# --------------------------------------------------------------------- ЦБ РФ
def _num(s: str) -> float:
    return float(str(s).strip().replace("\xa0", "").replace(" ", "").replace(",", "."))


def parse_cbr_xml(content: bytes) -> tuple[str, dict[str, float], dict[str, str]]:
    """XML ЦБ -> (фактическая дата YYYY-MM-DD, {валюта: руб. за 1 ед.}, котировки за N ед.)."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        try:   # кодировка не распознана — декодируем сами
            text = content.decode("cp1251")
            text = re.sub(r"^<\?xml[^>]*\?>", "", text.lstrip())
            root = ET.fromstring(text)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise RateUnavailable("ЦБ РФ: ответ не похож на XML курсов") from exc
    if root.tag != "ValCurs":
        raise RateUnavailable("ЦБ РФ: ответ не похож на XML курсов")
    raw_date = root.attrib.get("Date", "")
    try:
        eff = dt.datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat()
    except ValueError as exc:
        raise RateUnavailable(f"ЦБ РФ: непонятная дата в ответе «{raw_date}»") from exc
    rates: dict[str, float] = {}
    quoted: dict[str, str] = {}
    for v in root.findall("Valute"):
        code = (v.findtext("CharCode") or "").strip().upper()
        try:
            nominal = int(_num(v.findtext("Nominal") or "1"))
            value = _num(v.findtext("Value") or "")
        except ValueError:
            continue
        if not code or nominal <= 0 or value <= 0:
            continue
        rates[code] = value / nominal
        if nominal != 1:
            quoted[code] = f"{nominal} {code} = {value:g} RUB"
    return eff, rates, quoted


def fetch_cbr(on: dt.date, *, timeout: float = 15.0, max_back_days: int = 14) -> FetchResult:
    """Курсы ЦБ РФ на дату. Пустой ответ (дата слишком в будущем) -> RateUnavailable."""
    params = {"date_req": on.strftime("%d/%m/%Y")}
    content = http_request("GET", CBR_URL, params=params, timeout=timeout)
    eff, rates, quoted = parse_cbr_xml(content)
    if not rates:
        raise RateUnavailable(f"ЦБ РФ не вернул курсов на {on.isoformat()}")
    return FetchResult("cbr", on.isoformat(), eff, rates, quoted,
                       url=f"{CBR_URL}?date_req={params['date_req']}")


# --------------------------------------------------------------------- CFETS
_PAIR = re.compile(r"^(\d*)([A-Z]{3})/(\d*)([A-Z]{3})$")


def cfets_pair_to_cny(pair: str, price: float) -> tuple[str, float] | None:
    """Котировка CFETS -> (валюта, юаней за 1 единицу).

    «USD/CNY 6.7195» -> (USD, 6.7195); «100JPY/CNY 4.2381» -> (JPY, 0.042381);
    «CNY/RUB 12.2839» (рублей за 1 юань) -> (RUB, 1/12.2839).
    """
    m = _PAIR.match(pair.strip().upper())
    if not m or price <= 0:
        return None
    n_base = int(m.group(1) or 1)
    base = m.group(2)
    n_quote = int(m.group(3) or 1)
    quote = m.group(4)
    if quote == "CNY" and base != "CNY":
        return base, price * n_quote / n_base
    if base == "CNY" and quote != "CNY":
        return quote, n_base / (price * n_quote)
    return None


def _cfets_values(pairs: list[str], values: list) -> tuple[dict[str, float], dict[str, str]]:
    rates: dict[str, float] = {}
    quoted: dict[str, str] = {}
    for pair, raw in zip(pairs, values):
        try:
            price = _num(raw)
        except (TypeError, ValueError):
            continue          # «---» — пары в этот день не было
        conv = cfets_pair_to_cny(pair, price)
        if conv is None:
            continue
        code, value = conv
        rates[code] = value
        if pair.strip().upper() != f"{code}/CNY":     # котировка не «1 X = … CNY»
            quoted[code] = f"{pair.strip()} = {str(raw).strip()}"
    return rates, quoted


def parse_cfets_history(payload: dict) -> dict[str, tuple[dict[str, float], dict[str, str]]]:
    """JSON CcprHisNew -> {дата: ({валюта: юаней за 1 ед.}, котировки)}.

    Значения в records[].values идут в порядке data.searchlist (если в запросе был
    фильтр currency) — иначе в порядке data.head.
    """
    head = payload.get("head") or {}
    code = str(head.get("rep_code", "200"))
    if code != "200":
        raise RateUnavailable(f"CFETS: код ответа {code} {head.get('rep_message', '')}".strip())
    data = payload.get("data") or {}
    pairs = data.get("searchlist") or data.get("head") or []
    out = {}
    for rec in payload.get("records") or []:
        day = str(rec.get("date", ""))[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        rates, quoted = _cfets_values(pairs, rec.get("values") or [])
        if rates:
            out[day] = (rates, quoted)
    return out


def parse_cfets_current(payload: dict) -> tuple[str, dict[str, float], dict[str, str]]:
    """JSON ccpr.json (курс текущего дня) -> (дата, курсы, котировки)."""
    data = payload.get("data") or {}
    day = str(data.get("lastDate", ""))[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise RateUnavailable("CFETS: в ответе нет даты курса")
    recs = payload.get("records") or []
    pairs = [str(r.get("vrtEName", "")) for r in recs]
    values = [r.get("price") for r in recs]
    rates, quoted = _cfets_values(pairs, values)
    if not rates:
        raise RateUnavailable("CFETS: в ответе нет курсов")
    return day, rates, quoted


def _json(content: bytes, what: str) -> dict:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RateUnavailable(f"CFETS: {what} — ответ не JSON") from exc
    if not isinstance(payload, dict):
        raise RateUnavailable(f"CFETS: {what} — неожиданный формат ответа")
    return payload


def fetch_cfets(on: dt.date, *, timeout: float = 15.0, max_back_days: int = 14) -> FetchResult:
    """Центральный курс на дату: последний опубликованный в окне [on - max_back_days, on].

    Один запрос истории за всё окно; все полученные дни возвращаются в other_days
    (их тоже можно положить в кэш). Если история не ответила, а дата — сегодня или
    позже, пробуем ccpr.json (курс текущего дня).
    """
    start = on - dt.timedelta(days=max_back_days)
    params = {"startDate": start.isoformat(), "endDate": on.isoformat(),
              "currency": "", "pageNum": 1, "pageSize": 50}
    url = f"{CFETS_HISTORY_URL}?startDate={params['startDate']}&endDate={params['endDate']}"
    days: dict = {}
    hist_err: RateUnavailable | None = None
    try:
        payload = _json(http_request("POST", CFETS_HISTORY_URL, params=params, timeout=timeout),
                        "история")
        days = {d: v for d, v in parse_cfets_history(payload).items() if d <= on.isoformat()}
    except RateUnavailable as exc:
        hist_err = exc
    if not days and on >= today():
        try:
            day, rates, quoted = parse_cfets_current(
                _json(http_request("GET", CFETS_CURRENT_URL, timeout=timeout), "текущий курс"))
            if start.isoformat() <= day <= on.isoformat():
                days = {day: (rates, quoted)}
                url = CFETS_CURRENT_URL
        except RateUnavailable:
            pass
    if not days:
        if hist_err is not None:
            raise hist_err
        raise RateUnavailable(
            f"CFETS: нет центрального курса за {start.isoformat()} — {on.isoformat()}")
    eff = max(days)
    rates, quoted = days[eff]
    other = {d: {"rates": r, "quoted": q} for d, (r, q) in days.items()}
    return FetchResult("cfets", on.isoformat(), eff, rates, quoted, url=url, other_days=other)

