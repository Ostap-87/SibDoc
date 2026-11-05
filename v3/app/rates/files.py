# -*- coding: utf-8 -*-
"""SibDoc3: закрытый контур — курсы валют из файлов (флешка, другой носитель).

В закрытом контуре сайты ЦБ РФ и CFETS недоступны, поэтому курсы приносят файлами,
скачанными на компьютере с интернетом. Формат определяется по содержимому:

  cbr_daily    ЦБ РФ, XML_daily.asp (https://www.cbr.ru/scripts/XML_daily.asp?date_req=ДД/ММ/ГГГГ):
               все валюты на одну дату; атрибут Date — дата, на которую курс установлен.
  cbr_dynamic  ЦБ РФ, XML_dynamic.asp (…/XML_dynamic.asp?date_req1=…&date_req2=…&VAL_NM_RQ=R01235):
               одна валюта за период. Валюта — по коду ЦБ (R01235 = USD, R01375 = CNY …),
               по коду в имени файла (usd_2026.xml) или из параметра currency.
  cfets_table  CFETS / chinamoney.com.cn «人民币汇率中间价» (Excel или CSV): колонка даты
               (日期 / Date / Дата) и колонки пар USD/CNY, 100JPY/CNY, CNY/RUB …
  template     шаблон SibDoc (Excel или CSV, get_template_xlsx()): дата | валюта | курс | источник
               [| номинал | к валюте | комментарий]; источник: cbr (ЦБ РФ, рублей за номинал),
               cfets (юаней за номинал), china_customs (месячный курс таможни КНР),
               manual (ручной курс «номинал валюты = курс в валюте „к валюте“»).

Официальные курсы (cbr, cfets) кладутся в тот же кэш, что и скачанные из сети
(storage/rates/cache.json) с пометкой _imported = имя файла, поэтому проверки
работают с ними как с обычными. Дни внутри периода файла, на которые курс не
устанавливался (выходные, праздники), заполняются последним курсом до них — так же,
как отвечает сам ЦБ РФ. Курсы таможни КНР и ручные — в таблицу ручных курсов.
Каждая загрузка — запись «rate_import» в журнале (имя файла, SHA-256, что загружено).
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET

from app.journal import log
from app.rates import providers, storage
from app.rates.currency import is_iso, norm_currency

FORMAT_TITLES = {
    "cbr_daily": "ЦБ РФ, XML_daily (курсы на дату)",
    "cbr_dynamic": "ЦБ РФ, XML_dynamic (одна валюта за период)",
    "cfets_table": "CFETS 中间价 (таблица Excel/CSV)",
    "template": "шаблон SibDoc «дата, валюта, курс, источник»",
}
ACCEPTED = (".xml", ".xlsx", ".xls", ".csv", ".txt", ".zip")

# Внутренние коды валют ЦБ РФ (атрибут ID в XML_daily, VAL_NM_RQ в XML_dynamic).
CBR_IDS = {
    "R01010": "AUD", "R01020A": "AZN", "R01035": "GBP", "R01060": "AMD", "R01090B": "BYN",
    "R01100": "BGN", "R01115": "BRL", "R01135": "HUF", "R01150": "VND", "R01200": "HKD",
    "R01210": "GEL", "R01215": "DKK", "R01230": "AED", "R01235": "USD", "R01239": "EUR",
    "R01270": "INR", "R01280": "IDR", "R01335": "KZT", "R01350": "CAD", "R01355": "QAR",
    "R01370": "KGS", "R01375": "CNY", "R01500": "MDL", "R01530": "NZD", "R01535": "NOK",
    "R01565": "PLN", "R01585F": "RON", "R01589": "XDR", "R01625": "SGD", "R01670": "TJS",
    "R01675": "THB", "R01700J": "TRY", "R01710A": "TMT", "R01717": "UZS", "R01720": "UAH",
    "R01760": "CZK", "R01770": "SEK", "R01775": "CHF", "R01805F": "RSD", "R01810": "ZAR",
    "R01815": "KRW", "R01820": "JPY",
}

PIVOT = {"cbr": "RUB", "cfets": "CNY"}
SOURCE_TITLES = {"cbr": "ЦБ РФ", "cfets": "CFETS (中间价)", "china_customs": "таможня КНР",
                 "manual": "ручные"}

_SOURCE_ALIASES = {
    "cbr": ("cbr", "цб", "цбрф", "цб рф", "банк россии", "bank of russia", "俄罗斯央行", "俄央行"),
    "cfets": ("cfets", "цб кнр", "нбк", "pboc", "中间价", "人民币汇率中间价", "chinamoney", "cny parity"),
    "china_customs": ("china_customs", "cn_customs", "таможня кнр", "таможня", "海关",
                      "海关计征汇率", "customs"),
    "manual": ("manual", "ручной", "вручную", "手动", "any"),
}

_HEAD = {
    "date": ("дата", "date", "日期", "день", "надату", "на дату"),
    "currency": ("валюта", "currency", "币种", "кодвалюты", "код валюты", "ccy", "base"),
    "rate": ("курс", "rate", "value", "значение", "汇率", "中间价", "price"),
    "source": ("источник", "source", "来源", "тип"),
    "nominal": ("номинал", "nominal", "единиц", "unit", "单位"),
    "quote": ("квалюте", "к валюте", "котируемаявалюта", "котируемая валюта", "quote",
              "ввалюте", "в валюте", "计价货币", "валютакурса"),
    "comment": ("комментарий", "comment", "备注", "примечание"),
}


class RateFileError(ValueError):
    """Файл курсов не распознан или в нём нет курсов (текст — по-русски)."""


# ===================================================================== утилиты
def _rates_pkg():
    from app import rates            # поздний импорт: app.rates импортирует этот пакет
    return rates


def _day(v) -> str:
    return _rates_pkg()._norm_date(v).isoformat()


def _num(v) -> float:
    return _rates_pkg()._to_float(v)


def _key(s) -> str:
    return re.sub(r"[\s_\-.,:;()（）]+", "", str(s or "").strip().lower())


def _head_kind(cell) -> str | None:
    k = _key(cell)
    if not k:
        return None
    for kind, names in _HEAD.items():
        if k in {_key(n) for n in names}:
            return kind
    return None


def _source(v) -> str | None:
    s = str(v or "").strip().lower()
    for src, names in _SOURCE_ALIASES.items():
        if s in names or _key(s) in {_key(n) for n in names}:
            return src
    return None


def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() in ("", "nan", "None", "NaT")


# ===================================================================== XML ЦБ РФ
def _xml_root(content: bytes):
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        try:
            text = content.decode("cp1251")
            text = re.sub(r"^﻿?<\?xml[^>]*\?>", "", text.lstrip())
            return ET.fromstring(text)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise RateFileError("файл не похож на XML курсов ЦБ РФ") from exc


def _currency_from_name(filename: str) -> str:
    for token in re.findall(r"[A-Za-z]{3}", os.path.basename(filename or "")):
        code = token.upper()
        if code in set(CBR_IDS.values()) and code != "XML":
            return code
    return ""


def _parse_cbr(content: bytes, filename: str, currency: str | None) -> dict:
    root = _xml_root(content)
    if root.tag != "ValCurs":
        raise RateFileError("в XML нет элемента ValCurs — это не файл курсов ЦБ РФ")
    if root.findall("Valute"):                                   # XML_daily
        eff, rates, quoted = providers.parse_cbr_xml(content)
        if not rates:
            raise RateFileError(f"в файле ЦБ РФ на {eff} нет курсов")
        series = {c: {eff: v} for c, v in rates.items()}
        return {"format": "cbr_daily", "official": {"cbr": series},
                "quoted": {"cbr": {eff: quoted}}, "range": {"cbr": (eff, eff)}, "manual": []}
    records = root.findall("Record")
    if not records:
        raise RateFileError("в файле ЦБ РФ нет ни Valute (XML_daily), ни Record (XML_dynamic)")
    code = norm_currency(currency) if currency else ""
    cbr_id = root.attrib.get("ID") or records[0].attrib.get("Id") or ""
    if not code:
        code = CBR_IDS.get(cbr_id.strip().upper(), "") or _currency_from_name(filename)
    if not is_iso(code):
        raise RateFileError(f"не удалось определить валюту по коду ЦБ «{cbr_id}» — укажите валюту "
                            f"при загрузке (например, USD)")
    values: dict[str, float] = {}
    quoted: dict[str, dict] = {}
    for r in records:
        try:
            day = dt.datetime.strptime(r.attrib.get("Date", ""), "%d.%m.%Y").date().isoformat()
            nominal = int(providers._num(r.findtext("Nominal") or "1"))
            value = providers._num(r.findtext("Value") or "")
        except ValueError:
            continue
        if nominal > 0 and value > 0:
            values[day] = value / nominal
            if nominal != 1:
                quoted[day] = {code: f"{nominal} {code} = {value:g} RUB"}
    if not values:
        raise RateFileError("в файле XML_dynamic нет ни одной записи курса")
    lo, hi = min(values), max(values)
    try:
        lo = min(lo, _day(root.attrib["DateRange1"]))
        hi = max(hi, _day(root.attrib["DateRange2"]))
    except (KeyError, ValueError):
        pass
    return {"format": "cbr_dynamic", "official": {"cbr": {code: values}}, "quoted": {"cbr": quoted},
            "range": {"cbr": (lo, hi)}, "manual": []}


# ===================================================================== таблицы (Excel / CSV)
def _read_tables(content: bytes, filename: str) -> list[list[list]]:
    """Файл -> список листов, лист — список строк (значения как есть)."""
    name = (filename or "").lower()
    if content[:2] == b"PK" or content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" \
            or name.endswith((".xlsx", ".xls")):
        import pandas as pd
        try:
            book = pd.read_excel(io.BytesIO(content), header=None, sheet_name=None, dtype=object)
        except Exception as exc:  # noqa: BLE001 — повреждённый или неподдерживаемый файл
            raise RateFileError(f"не удалось прочитать Excel: {exc}") from exc
        return [df.where(df.notna(), None).values.tolist() for df in book.values()]
    from app.connectors.fileutil import smart_decode
    text = smart_decode(content).lstrip("﻿")
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    return [[row for row in csv.reader(io.StringIO(text), delimiter=delim)]]


_PAIR_HEAD = re.compile(r"^\s*(\d*)\s*([^/\d][^/]*?)\s*/\s*(\d*)\s*([^/\d][^/]*?)\s*$")


def _pair(cell) -> str | None:
    """Заголовок колонки CFETS -> каноническая пара «100JPY/CNY» (или None)."""
    m = _PAIR_HEAD.match(str(cell or ""))
    if not m:
        return None
    a, b = norm_currency(m.group(2)), norm_currency(m.group(4))
    if not (is_iso(a) and is_iso(b)) or "CNY" not in (a, b) or a == b:
        return None
    return f"{m.group(1)}{a}/{m.group(3)}{b}"


def _parse_cfets(rows: list[list], hi: int) -> dict | None:
    head = rows[hi]
    date_col = next((i for i, c in enumerate(head) if _head_kind(c) == "date"), None)
    pairs = {i: _pair(c) for i, c in enumerate(head)}
    pairs = {i: p for i, p in pairs.items() if p}
    if date_col is None or not pairs:
        return None
    series: dict[str, dict[str, float]] = {}
    quoted: dict[str, dict[str, str]] = {}
    for row in rows[hi + 1:]:
        if date_col >= len(row) or _empty(row[date_col]):
            continue
        try:
            day = _day(row[date_col])
        except ValueError:
            continue
        for i, pair in pairs.items():
            if i >= len(row) or _empty(row[i]):
                continue
            try:
                price = _num(row[i])
            except (TypeError, ValueError):
                continue                                  # «---» — пары в этот день не было
            conv = providers.cfets_pair_to_cny(pair, price)
            if conv is None:
                continue
            code, value = conv
            series.setdefault(code, {})[day] = value
            if pair != f"{code}/CNY":
                quoted.setdefault(day, {})[code] = f"{pair} = {row[i]}"
    if not series:
        raise RateFileError("в таблице CFETS нет ни одного курса")
    days = sorted({d for s in series.values() for d in s})
    return {"format": "cfets_table", "official": {"cfets": series}, "quoted": {"cfets": quoted},
            "range": {"cfets": (days[0], days[-1])}, "manual": []}


def _parse_template(rows: list[list], hi: int, default_source: str | None) -> dict | None:
    cols: dict[str, int] = {}
    for i, c in enumerate(rows[hi]):
        kind = _head_kind(c)
        if kind and kind not in cols:
            cols[kind] = i
    if not {"date", "currency", "rate"} <= set(cols):
        return None
    official: dict[str, dict[str, dict[str, float]]] = {}
    quoted: dict[str, dict[str, dict[str, str]]] = {}
    manual: list[dict] = []
    warnings: list[str] = []

    def cell(row, kind):
        i = cols.get(kind)
        return row[i] if i is not None and i < len(row) else None

    for n, row in enumerate(rows[hi + 1:], start=hi + 2):
        if all(_empty(v) for v in row):
            continue
        try:
            day = _day(cell(row, "date"))
            base = norm_currency(cell(row, "currency"))
            value = _num(cell(row, "rate"))
            nominal = _num(cell(row, "nominal")) if not _empty(cell(row, "nominal")) else 1.0
        except (TypeError, ValueError) as exc:
            warnings.append(f"строка {n}: пропущена — {exc}")
            continue
        src_raw = cell(row, "source")
        src = _source(src_raw) if not _empty(src_raw) else (default_source or None)
        if src is None:
            warnings.append(f"строка {n}: неизвестный источник «{src_raw or ''}» "
                            f"(cbr | cfets | china_customs | manual)")
            continue
        quote = norm_currency(cell(row, "quote")) if not _empty(cell(row, "quote")) else ""
        if not is_iso(base) or (quote and not is_iso(quote)):
            warnings.append(f"строка {n}: непонятная валюта «{cell(row, 'currency')}»")
            continue
        if not (value > 0 and nominal > 0):
            warnings.append(f"строка {n}: курс и номинал должны быть больше нуля")
            continue
        per_unit = value / nominal
        comment = "" if _empty(cell(row, "comment")) else str(cell(row, "comment"))
        if src in PIVOT:
            pivot = PIVOT[src]
            q = quote or pivot
            if base == pivot and q != pivot:          # «1 CNY = 12.28 RUB» для ЦБ РФ -> RUB за 1 …
                base, per_unit = q, 1.0 / per_unit
            elif q != pivot:
                warnings.append(f"строка {n}: курс {SOURCE_TITLES[src]} должен быть в {pivot}, "
                                f"а указан {base}/{q}")
                continue
            if base == pivot:
                continue
            official.setdefault(src, {}).setdefault(base, {})[day] = per_unit
            if nominal != 1:
                quoted.setdefault(src, {}).setdefault(day, {})[base] = \
                    f"{nominal:g} {base} = {value:g} {pivot}"
        elif src == "china_customs":
            manual.append({"source_scope": "china_customs", "base": base, "quote": quote or "CNY",
                           "date": None, "month": day[:7], "value": per_unit, "comment": comment})
        else:
            if not quote:
                warnings.append(f"строка {n}: для ручного курса нужна колонка «к валюте»")
                continue
            manual.append({"source_scope": "any", "base": base, "quote": quote, "date": day,
                           "month": None, "value": per_unit, "comment": comment})
    ranges = {}
    for src, series in official.items():
        days = sorted({d for s in series.values() for d in s})
        ranges[src] = (days[0], days[-1])
    if not official and not manual:
        raise RateFileError("в шаблоне нет ни одного курса" + (f": {warnings[0]}" if warnings else ""))
    return {"format": "template", "official": official, "quoted": quoted, "range": ranges,
            "manual": manual, "warnings": warnings}


def _parse_table(content: bytes, filename: str, source: str | None) -> dict:
    sheets = _read_tables(content, filename)
    for rows in sheets:
        for hi, row in enumerate(rows[:30]):
            kinds = {_head_kind(c) for c in row}
            if "date" not in kinds:
                continue
            if any(_pair(c) for c in row):
                got = _parse_cfets(rows, hi)
                if got:
                    return got
            if {"currency", "rate"} <= kinds:
                got = _parse_template(rows, hi, source)
                if got:
                    return got
    raise RateFileError("таблица не распознана: нужна колонка даты и колонки пар CFETS (USD/CNY …) "
                        "или колонки шаблона «дата, валюта, курс, источник»")


def parse(content: bytes, filename: str, *, currency: str | None = None,
          source: str | None = None) -> dict:
    """Распознать файл курсов (без записи). -> {format, official, quoted, range, manual, warnings}."""
    if not content:
        raise RateFileError("пустой файл")
    src = _source(source) if source else None
    head = content[:400].lstrip(b"\xef\xbb\xbf \r\n\t")
    if head.startswith(b"<") or (filename or "").lower().endswith(".xml"):
        got = _parse_cbr(content, filename, currency)
    else:
        got = _parse_table(content, filename, src)
    got.setdefault("warnings", [])
    return got


# ===================================================================== запись
def _fill(series: dict[str, float], lo: str, hi: str, max_back: int) -> dict[str, tuple[float, str]]:
    """Ряд одной валюты {дата: курс} -> {каждый день периода: (курс, фактическая дата)}.

    День без курса (выходной, праздник) берёт последний курс до него, но не старше max_back.
    """
    out: dict[str, tuple[float, str]] = {}
    days = sorted(series)
    if not days:
        return out
    cur = dt.date.fromisoformat(lo)
    end = dt.date.fromisoformat(hi)
    idx, last = 0, None
    while cur <= end:
        ds = cur.isoformat()
        while idx < len(days) and days[idx] <= ds:
            last = days[idx]
            idx += 1
        if last is not None and (cur - dt.date.fromisoformat(last)).days <= max_back:
            out[ds] = (series[last], last)
        cur += dt.timedelta(days=1)
    return out


def _write_official(src: str, series: dict, quoted: dict, rng: tuple, name: str,
                    max_back: int) -> dict:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    per_day: dict[str, dict] = {}
    for code, values in series.items():
        for day, (value, eff) in _fill(values, rng[0], rng[1], max_back).items():
            rec = per_day.setdefault(day, {"_vals": {}, "_effs": set(), "_exact": set()})
            rec["_vals"][code] = value
            rec["_effs"].add(eff)
            if eff == day:
                rec["_exact"].add(code)
    to_cache: dict[str, dict] = {}
    with storage.LOCK:
        for day, part in per_day.items():
            old = storage.cache_get(src, day) or {}
            new = {k: v for k, v in old.items()}
            for code, value in part["_vals"].items():
                # точный курс файла важнее старого; заполненный выходной — только если валюты не было
                if code in part["_exact"] or not isinstance(old.get(code), (int, float)):
                    new[code] = value
            effs = set(part["_effs"])
            if old.get("effective_date"):
                effs.add(str(old["effective_date"]))
            new["effective_date"] = max(effs)        # дата последней публикации в записи
            new["_fetched"] = stamp
            new["_imported"] = name
            q = dict(old.get("_quoted") or {})
            for eff in part["_effs"]:
                q.update((quoted or {}).get(eff, {}))
            if q:
                new["_quoted"] = dict(sorted(q.items()))
            new.pop("_provisional", None)
            to_cache[day] = dict(sorted(new.items()))
        storage.cache_put(src, to_cache)
    currencies = sorted(series)
    return {"source": src, "title": SOURCE_TITLES[src], "currencies": currencies,
            "from": rng[0], "to": rng[1], "days": len(to_cache)}


def _write_manual(entries: list[dict], name: str, user: str) -> int:
    if not entries:
        return 0
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with storage.LOCK:
        cur = storage.manual_load()
        index = {(e.get("source_scope"), e.get("base"), e.get("quote"), e.get("date"),
                  e.get("month")): i for i, e in enumerate(cur)}
        for e in entries:
            rec = {"id": uuid.uuid4().hex[:12], **e, "user": user or "-", "ts": stamp,
                   "comment": (e.get("comment") or f"из файла {name}")[:300]}
            key = (rec["source_scope"], rec["base"], rec["quote"], rec["date"], rec["month"])
            if key in index:
                rec["id"] = cur[index[key]].get("id") or rec["id"]
                cur[index[key]] = rec
            else:
                index[key] = len(cur)
                cur.append(rec)
        storage.manual_save(cur)
    return len(entries)


def import_file(content: bytes, filename: str, *, user: str = "-", currency: str | None = None,
                source: str | None = None) -> dict:
    """Загрузить курсы из одного файла (XML ЦБ РФ, таблица CFETS, шаблон). -> отчёт."""
    name = os.path.basename(filename or "файл")
    got = parse(content, name, currency=currency, source=source)
    max_back = _rates_pkg().load_settings()["max_back_days"]
    loaded = [_write_official(src, series, got["quoted"].get(src, {}), got["range"][src], name, max_back)
              for src, series in got["official"].items() if series]
    n_manual = _write_manual(got["manual"], name, user)
    _rates_pkg()._reset_memory()                      # забыть «нет курса» до загрузки
    sha = hashlib.sha256(content).hexdigest()
    parts = [f"{x['title']} — валют {len(x['currencies'])} ({', '.join(x['currencies'][:8])}"
             f"{' …' if len(x['currencies']) > 8 else ''}), {x['from']} … {x['to']}" for x in loaded]
    if n_manual:
        parts.append(f"ручных записей: {n_manual}")
    detail = f"Курсы из файла «{name}» ({FORMAT_TITLES[got['format']]}): " + "; ".join(parts)
    if got["warnings"]:
        detail += f"; пропущено строк: {len(got['warnings'])}"
    log("rate_import", user, detail,
        meta={"file": name, "sha256": sha, "format": got["format"], "loaded": loaded,
              "manual": n_manual, "warnings": got["warnings"][:50]})
    return {"file": name, "ok": True, "format": got["format"], "format_title": FORMAT_TITLES[got["format"]],
            "sha256": sha, "loaded": loaded, "manual": n_manual, "warnings": got["warnings"][:50]}


def import_files(files: list[tuple[bytes, str]], *, user: str = "-", currency: str | None = None,
                 source: str | None = None) -> list[dict]:
    """Несколько файлов (zip раскрывается); ошибка одного файла не мешает остальным."""
    from app.connectors.fileutil import extract_zip
    queue: list[tuple[bytes, str]] = []
    out: list[dict] = []
    for data, name in files:
        if (name or "").lower().endswith(".zip"):
            try:
                entries, notes = extract_zip(data)
            except ValueError as exc:
                out.append({"file": name, "ok": False, "error": str(exc)})
                continue
            queue += [(d, f"{name}/{n}") for n, d in entries if n.lower().endswith(ACCEPTED)]
            out += [{"file": name, "ok": False, "error": n} for n in notes]
        else:
            queue.append((data, name))
    for data, name in queue:
        try:
            out.append(import_file(data, name, user=user, currency=currency, source=source))
        except (RateFileError, ValueError) as exc:
            out.append({"file": os.path.basename(name), "ok": False, "error": str(exc)})
    return out


def template_xlsx() -> bytes:
    """Шаблон Excel «дата, валюта, курс, источник» с примерами и пояснением."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Курсы"
    ws.append(["дата", "валюта", "курс", "источник", "номинал", "к валюте", "комментарий"])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(["2026-10-15", "USD", 83.9497, "cbr", 1, "RUB", "ЦБ РФ: рублей за номинал"])
    ws.append(["2026-10-15", "JPY", 52.9852, "cbr", 100, "RUB", "100 иен"])
    ws.append(["2026-10-15", "USD", 7.0965, "cfets", 1, "CNY", "CFETS 中间价: юаней за номинал"])
    ws.append(["2026-10-01", "USD", 7.1, "china_customs", 1, "CNY", "таможня КНР: курс на месяц"])
    ws.append(["2026-10-15", "KZT", 0.0145, "manual", 1, "CNY", "ручной курс"])
    for col, width in zip("ABCDEFG", (12, 9, 10, 15, 10, 10, 34)):
        ws.column_dimensions[col].width = width
    info = wb.create_sheet("Как заполнить")
    for line in (
        "Одна строка — один курс. Даты: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.",
        "источник: cbr — ЦБ РФ (рублей за номинал валюты); cfets — центральный курс НБК / CFETS "
        "(юаней за номинал); china_customs — месячный курс таможни КНР (месяц берётся из даты); "
        "manual — ручной курс (нужна колонка «к валюте»).",
        "номинал — за сколько единиц валюты указан курс (по умолчанию 1).",
        "Дни без курса (выходные) внутри периода файла заполняются последним курсом до них.",
        "Загрузка: SibDoc → «Курсы валют» → «Курсы из файла».",
    ):
        info.append([line])
    info.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
