# -*- coding: utf-8 -*-
"""Курсы валют для проверок: пересчёт цен CNY / USD / RUB / EUR … на дату декларации.

По умолчанию курсы берутся с официальных платформ (ЦБ РФ — российская сторона,
ЦБ КНР / CFETS — китайская, месячный курс таможни КНР — из ручной таблицы),
кэшируются в storage/rates/cache.json. Ручные курсы пользователя —
storage/rates/manual.json. Режим и стороны — config/rates.yaml.

Режимы:
- official        — официальный курс; ручной — только если официальный недоступен
                    (пометка «ручной: официальный недоступен»);
- manual_override — ручная запись на дату или на месяц важнее официальной.

Публичный API:
    get_rate(base, quote, on, side="ru", source=None) -> RateInfo
    convert(amount, from_ccy, to_ccy, on, side="ru", source=None) -> (сумма, RateInfo)
    refresh(source, on) -> {валюта: значение}
    list_rates(on, source=None) -> [строки для таблицы интерфейса]
    set_manual(...) / delete_manual(id) / list_manual()
    load_settings() / save_settings(d, user)
    norm_currency(v) -> «USD»

Пример:
    from app import rates
    usd = rates.get_rate("USD", "RUB", "2026-10-15")          # ЦБ РФ
    cny, info = rates.convert(1000, "USD", "CNY", "2026-10-15", side="cn")  # CFETS
"""
from __future__ import annotations

import datetime as dt
import re
import time
import uuid
from dataclasses import asdict, dataclass

from app.journal import log
from app.rates import providers, storage
from app.rates.currency import is_iso, norm_currency
from app.rates.providers import (SOURCE_INFO, SOURCES, RateInputError, RateUnavailable,
                                 SourceDown)

__all__ = [
    "RateInfo", "RateUnavailable", "RateInputError", "SOURCES", "SOURCE_INFO", "SCOPES",
    "get_rate", "convert", "refresh", "list_rates", "set_manual", "delete_manual",
    "list_manual", "load_settings", "save_settings", "norm_currency",
]

SCOPES = ("any", "cbr", "cfets", "china_customs")     # область действия ручной записи
PIVOT = {"cbr": "RUB", "cfets": "CNY", "china_customs": "CNY"}

FAIL_TTL = 300        # с: не повторять неудачный запрос той же даты
DOWN_TTL = 120        # с: не ходить к источнику после обрыва связи
_FAILS: dict[tuple[str, str], tuple[float, str]] = {}
_DOWN: dict[str, tuple[float, str]] = {}


@dataclass
class RateInfo:
    """Курс: 1 base = value quote."""
    base: str
    quote: str
    value: float
    date: str            # запрошенная дата YYYY-MM-DD
    effective_date: str  # дата, на которую реально опубликован курс
    source: str          # cbr | cfets | china_customs | manual
    manual: bool
    note: str = ""       # «кросс-курс через RUB», «ручной: официальный недоступен» …

    def as_dict(self) -> dict:
        d = asdict(self)
        d["text"] = f"1 {self.base} = {self.value:.6g} {self.quote}"
        return d


@dataclass
class _Leg:
    """Курс одной валюты к опорной (RUB или CNY): value опорной за 1 единицу."""
    value: float
    origin: str          # official | table | manual
    source: str          # cbr | cfets | china_customs | manual
    effective: str
    label: str


# ===================================================================== утилиты
def _title(src: str) -> str:
    return SOURCE_INFO.get(src, {}).get("title", src)


def _norm_date(on) -> dt.date:
    """Дата из date / datetime / pandas.Timestamp / строки (2026-10-15, 15.10.2026, 20261015)."""
    try:
        if on is None or on != on:                         # None, NaN, NaT
            raise RateInputError("не указана дата курса")
    except (TypeError, ValueError):
        pass
    if isinstance(on, dt.datetime):
        return on.date()
    if isinstance(on, dt.date):
        return on
    if hasattr(on, "to_pydatetime"):                       # pandas.Timestamp
        try:
            return on.to_pydatetime().date()
        except (ValueError, TypeError):
            pass
    s = str(on if on is not None else "").strip()
    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    for rx, order in ((r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", "ymd"),
                      (r"(\d{1,2})[./](\d{1,2})[./](\d{4})", "dmy"),
                      (r"(\d{4})(\d{2})(\d{2})", "ymd")):
        m = re.fullmatch(rx + r"(?:[ T].*)?", s)
        if m:
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return dt.date(y, mo, d)
            except ValueError:
                break
    raise RateInputError(f"не понимаю дату «{on}»: нужен формат ГГГГ-ММ-ДД")


def _norm_month(v) -> str:
    """«2026-10», «2026.10», «10.2026», «202610», date -> «2026-10»."""
    if isinstance(v, (dt.date, dt.datetime)):
        return f"{v.year:04d}-{v.month:02d}"
    s = str(v or "").strip()
    for rx, order in ((r"(\d{4})[-./](\d{1,2})", "ym"), (r"(\d{1,2})[-./](\d{4})", "my"),
                      (r"(\d{4})(\d{2})", "ym"), (r"(\d{4})年(\d{1,2})月", "ym")):
        m = re.fullmatch(rx, s)
        if m:
            a, b = (int(x) for x in m.groups())
            y, mo = (a, b) if order == "ym" else (b, a)
            if 1 <= mo <= 12:
                return f"{y:04d}-{mo:02d}"
    raise ValueError(f"не понимаю месяц «{v}»: нужен формат ГГГГ-ММ")


def _to_float(v) -> float:
    """Число из 12.5 / «12,5» / «1 234,56»."""
    if isinstance(v, bool):
        raise ValueError("ожидается число")
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().replace("\xa0", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return float(s)


def _ccy(v, what: str) -> str:
    code = norm_currency(v)
    if not is_iso(code):
        raise RateInputError(f"непонятная валюта ({what}): «{v}»")
    return code


def _round(v: float) -> float:
    return float(f"{v:.12g}")


def _pick_source(side: str, source: str | None, settings: dict) -> str:
    if source:
        src = str(source).strip().lower()
        if src not in SOURCES:
            raise ValueError(f"неизвестный источник «{source}»: {' | '.join(SOURCES)}")
        return src
    key = str(side or "ru").strip().lower()
    sides = settings.get("sides") or {}
    if key in sides:
        return sides[key]
    if key in SOURCES:
        return key
    raise ValueError(f"неизвестная сторона «{side}»: ожидается {' | '.join(sides) or 'ru, cn'}")


# ===================================================================== официальные курсы
def _reset_memory() -> None:
    """Забыть неудачные попытки загрузки (для тестов и кнопки «Обновить»)."""
    _FAILS.clear()
    _DOWN.clear()


def _record_rates(rec: dict) -> dict[str, float]:
    return {k: float(v) for k, v in rec.items() if is_iso(k) and isinstance(v, (int, float))}


def _check_gap(src: str, ds: str, eff: str, max_back: int) -> None:
    gap = (dt.date.fromisoformat(ds) - dt.date.fromisoformat(eff)).days
    if gap > max_back:
        raise RateUnavailable(
            f"{_title(src)}: последний курс до {ds} опубликован {eff} — "
            f"раньше, чем за {max_back} дн. (max_back_days)")


def _fetch(src: str, d: dt.date, settings: dict, *, user: str = "-", force: bool = False) -> dict:
    """Скачать курс источника на дату, положить в кэш, записать в журнал."""
    ds = d.isoformat()
    now = time.monotonic()
    if not force:
        down = _DOWN.get(src)
        if down and now - down[0] < DOWN_TTL:
            raise SourceDown(down[1])
        fail = _FAILS.get((src, ds))
        if fail and now - fail[0] < FAIL_TTL:
            raise RateUnavailable(fail[1])
    fetcher = getattr(providers, f"fetch_{src}")
    try:
        res = fetcher(d, timeout=settings["http_timeout"], max_back_days=settings["max_back_days"])
    except RateUnavailable as exc:
        msg = str(exc)
        _FAILS[(src, ds)] = (now, msg)
        if isinstance(exc, SourceDown):
            _DOWN[src] = (now, msg)
        log("rate_fetch", user, f"Не удалось загрузить курсы «{_title(src)}» на {ds}: {msg}",
            meta={"source": src, "date": ds, "ok": False, "error": msg})
        raise
    _FAILS.pop((src, ds), None)
    _DOWN.pop(src, None)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")

    def make(rates: dict, quoted: dict, eff: str) -> dict:
        rec = dict(sorted(rates.items()))
        rec["effective_date"] = eff
        rec["_fetched"] = stamp
        if quoted:
            rec["_quoted"] = dict(sorted(quoted.items()))
        return rec

    rec = make(res.rates, res.quoted, res.effective_date)
    final = res.effective_date == ds or d < providers.today()
    to_cache: dict[str, dict] = {}
    for day, payload in res.other_days.items():          # прочие дни того же ответа (CFETS)
        to_cache[day] = make(payload["rates"], payload.get("quoted", {}), day)
    if res.effective_date not in to_cache:
        to_cache[res.effective_date] = make(res.rates, res.quoted, res.effective_date)
    if final:
        to_cache[ds] = rec
    storage.cache_put(src, to_cache)
    shift = "" if res.effective_date == ds else f", фактическая дата курса {res.effective_date}"
    prov = "" if final else " (курс на дату ещё не опубликован — взят последний, в кэш не сохранён)"
    log("rate_fetch", user,
        f"Загружены курсы «{_title(src)}» на {ds}{shift}, валют: {len(res.rates)}{prov}",
        meta={"source": src, "date": ds, "effective_date": res.effective_date,
              "currencies": len(res.rates), "url": res.url, "ok": True, "final": final,
              "forced": force})
    rec = dict(rec)
    rec["_provisional"] = not final
    return rec


def _official_record(src: str, d: dt.date, settings: dict) -> dict:
    """Запись курсов cbr / cfets на дату: из кэша, иначе из сети (если не offline)."""
    ds = d.isoformat()
    rec = storage.cache_get(src, ds)
    if rec is None:
        if settings.get("offline"):
            raise RateUnavailable(f"офлайн-режим: курсов «{_title(src)}» на {ds} нет в кэше")
        rec = _fetch(src, d, settings)
    eff = str(rec.get("effective_date") or ds)
    _check_gap(src, ds, eff, settings["max_back_days"])
    return rec


# ===================================================================== ручные курсы
def _entry_rank(e: dict, d: dt.date, max_back: int) -> int:
    """3 — запись на эту дату, 2 — на этот месяц, 1 — на более раннюю дату в окне, 0 — нет."""
    ds = d.isoformat()
    if e.get("date"):
        if e["date"] == ds:
            return 3
        try:
            gap = (d - dt.date.fromisoformat(e["date"])).days
        except ValueError:
            return 0
        return 1 if 0 < gap <= max_back else 0
    if e.get("month") and e["month"] == ds[:7]:
        return 2
    return 0


def _entry_eff(e: dict) -> str:
    return e.get("date") or f"{e.get('month')}-01"


def _entry_label(e: dict, origin: str) -> str:
    when = e.get("date") or f"месяц {e.get('month')}"
    who = "таблица таможни КНР" if origin == "table" else "ручной"
    return f"{who} ({when}, #{e.get('id', '')})"


def _manual_layer(entries: list[dict], d: dt.date, pivot: str, scopes: tuple,
                  max_back: int, min_rank: int = 1, origin: str = "manual",
                  source: str = "manual") -> tuple[dict, dict]:
    """Ручные записи на дату -> (курсы к опорной валюте {валюта: _Leg}, прямые пары {(b, q): _Leg}).

    Приоритет: на эту дату > на месяц > более ранняя дата; запись конкретного источника
    важнее записи «any»; прямая запись важнее перевёрнутой.
    """
    best_leg: dict[str, tuple[tuple, _Leg]] = {}
    best_pair: dict[tuple[str, str], tuple[tuple, _Leg]] = {}

    def put(store: dict, key, prio: tuple, leg: _Leg):
        cur = store.get(key)
        if cur is None or prio > cur[0]:
            store[key] = (prio, leg)

    for e in entries:
        scope = e.get("source_scope", "any")
        if scope not in scopes:
            continue
        rank = _entry_rank(e, d, max_back)
        if rank < min_rank:
            continue
        try:
            b, q, v = e["base"], e["quote"], float(e["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if v <= 0 or b == q:
            continue
        specific = 0 if scope == "any" else 1
        when = e.get("date") or e.get("month") or ""
        eff = _entry_eff(e)
        label = _entry_label(e, origin)
        for direct, (x, y, val) in ((1, (b, q, v)), (0, (q, b, 1.0 / v))):
            prio = (rank, specific, direct, when, e.get("ts", ""))
            put(best_pair, (x, y), prio, _Leg(val, origin, source, eff, label))
            if y == pivot:
                put(best_leg, x, prio, _Leg(val, origin, source, eff, label))
    return ({k: v[1] for k, v in best_leg.items()}, {k: v[1] for k, v in best_pair.items()})


# ===================================================================== расчёт
def _combine(b: str, q: str, pivot: str, layers: list[dict]) -> tuple[float, list[_Leg]] | None:
    """Курс b->q через опорную валюту по первым найденным «ногам» из слоёв."""
    def leg(c: str):
        if c == pivot:
            return None, True
        for layer in layers:
            if layer and c in layer:
                return layer[c], True
        return None, False

    lb, okb = leg(b)
    lq, okq = leg(q)
    if not (okb and okq):
        return None
    vb = lb.value if lb else 1.0
    vq = lq.value if lq else 1.0
    return vb / vq, [x for x in (lb, lq) if x is not None]


def _make_info(b, q, value, ds, legs: list[_Leg], src: str, notes: list[str],
               pivot: str) -> RateInfo:
    manual_any = any(l.origin == "manual" for l in legs)
    eff = min((l.effective for l in legs), default=ds)
    parts = list(notes)
    if b != pivot and q != pivot and len(legs) == 2:
        parts.append(f"кросс-курс через {pivot}")
    origins = {(l.origin, l.source) for l in legs}
    if len(origins) > 1:                       # ноги из разных источников — назовём каждую
        parts.append("; ".join(
            f"{c}: {l.label if l.origin != 'official' else _title(l.source)}"
            for c, l in zip([c for c in (b, q) if c != pivot], legs)))
    elif legs and legs[0].origin in ("manual", "table"):
        parts.append(legs[0].label if len(legs) == 1 or legs[0].label == legs[-1].label
                     else ", ".join(l.label for l in legs))
    return RateInfo(b, q, _round(value), ds, eff,
                    "manual" if manual_any else src,
                    manual_any or any(l.origin == "table" for l in legs),
                    "; ".join(p for p in parts if p))


def _resolve(b: str, q: str, d: dt.date, src: str, side: str, settings: dict) -> RateInfo:
    ds = d.isoformat()
    max_back = settings["max_back_days"]
    entries = storage.manual_load()

    if src == "manual":                         # только ручные курсы, любые области
        pivots = ("RUB", "CNY") if str(side).lower() == "ru" else ("CNY", "RUB")
        for pivot in pivots:
            legs, pairs = _manual_layer(entries, d, pivot, SCOPES, max_back)
            if (b, q) in pairs:
                return _make_info(b, q, pairs[(b, q)].value, ds, [pairs[(b, q)]], src, [], "")
            got = _combine(b, q, pivot, [legs])
            if got:
                return _make_info(b, q, got[0], ds, got[1], src, [], pivot)
        raise RateUnavailable(f"нет ручного курса {b}/{q} на {ds}")

    pivot = PIVOT[src]
    # --- официальный слой
    off_legs: dict | None = None
    off_notes: list[str] = []
    off_err = ""
    if src == "china_customs":
        off_legs, _ = _manual_layer(entries, d, pivot, ("china_customs",), 0, min_rank=2,
                                    origin="table", source="china_customs")
        if not off_legs:
            off_err = (f"курс таможни КНР на {ds[:7]} не задан — добавьте его в ручную таблицу "
                       f"(source_scope=china_customs, month={ds[:7]})")
            off_legs = None
    else:
        try:
            rec = _official_record(src, d, settings)
            eff = str(rec.get("effective_date") or ds)
            off_legs = {c: _Leg(v, "official", src, eff, _title(src))
                        for c, v in _record_rates(rec).items()}
            if rec.get("_provisional"):
                off_notes.append(f"курс на {ds} ещё не опубликован — взят последний от {eff}")
            elif eff != ds:
                off_notes.append(f"курс от {eff} (на {ds} новый курс не устанавливался)")
        except RateUnavailable as exc:
            off_err = str(exc)

    # --- ручной слой: для china_customs — только записи «any» (таблица таможни — выше)
    scopes = ("any",) if src == "china_customs" else (src, "any")
    man_all, pairs_all = _manual_layer(entries, d, pivot, scopes, max_back, min_rank=1)
    off_layers = [off_legs] if off_legs else []

    def missing_reason() -> str:
        if off_err:
            return off_err
        absent = [c for c in (b, q) if c != pivot and not (off_legs and c in off_legs)]
        return f"в курсах «{_title(src)}» на {ds} нет валюты {', '.join(absent)}"

    if settings["mode"] == "manual_override":
        man_strong, pairs_strong = _manual_layer(entries, d, pivot, scopes, max_back, min_rank=2)
        note = "ручной курс важнее официального (режим manual_override)"
        if (b, q) in pairs_strong:
            return _make_info(b, q, pairs_strong[(b, q)].value, ds, [pairs_strong[(b, q)]],
                              src, [note], "")
        got = _combine(b, q, pivot, [man_strong] + off_layers)
        if got:
            used_manual = any(l.origin == "manual" for l in got[1])
            return _make_info(b, q, got[0], ds, got[1], src,
                              ([note] if used_manual else off_notes), pivot)
    else:
        got = _combine(b, q, pivot, off_layers)
        if got:
            return _make_info(b, q, got[0], ds, got[1], src, off_notes, pivot)

    # --- запасной вариант: ручной курс, официальный недоступен
    fallback = f"ручной: официальный недоступен ({missing_reason()})"
    if (b, q) in pairs_all:
        return _make_info(b, q, pairs_all[(b, q)].value, ds, [pairs_all[(b, q)]], src,
                          [fallback], "")
    got = _combine(b, q, pivot, off_layers + [man_all])
    if got and any(l.origin == "manual" for l in got[1]):
        return _make_info(b, q, got[0], ds, got[1], src, [fallback], pivot)
    raise RateUnavailable(f"нет курса {b}/{q} на {ds}: {missing_reason()}")


# ===================================================================== публичный API
def get_rate(base: str, quote: str, on, side: str = "ru", source: str | None = None) -> RateInfo:
    """Курс «1 base = value quote» на дату on.

    side: ru -> ЦБ РФ, cn -> CFETS, cn_customs -> таможня КНР (см. config/rates.yaml → sides);
    source: явно выбрать cbr | cfets | china_customs | manual (важнее side).
    Нет курса -> RateUnavailable (в тексте — причина по-русски).
    """
    b = _ccy(base, "base")
    q = _ccy(quote, "quote")
    d = _norm_date(on)
    settings = load_settings()
    src = _pick_source(side, source, settings)
    if b == q:
        return RateInfo(b, q, 1.0, d.isoformat(), d.isoformat(), src, False, "одна и та же валюта")
    return _resolve(b, q, d, src, side, settings)


def convert(amount: float, from_ccy: str, to_ccy: str, on, side: str = "ru",
            source: str | None = None) -> tuple[float, RateInfo]:
    """Пересчитать сумму: (amount × курс, RateInfo). Сумма не округляется."""
    try:
        value = _to_float(amount)
    except (TypeError, ValueError):
        raise RateInputError(f"непонятная сумма «{amount}»") from None
    info = get_rate(from_ccy, to_ccy, on, side=side, source=source)
    return value * info.value, info


def refresh(source: str, on, *, user: str = "-") -> dict:
    """Принудительно скачать курс источника на дату (кнопка «Обновить»).

    cbr / cfets -> {валюта: значение в RUB / CNY за 1 единицу}, кэш обновляется,
    событие пишется в журнал. china_customs -> текущая ручная таблица месяца (без сети).
    """
    src = str(source or "").strip().lower()
    d = _norm_date(on)
    settings = load_settings()
    if src == "china_customs":
        legs, _ = _manual_layer(storage.manual_load(), d, "CNY", ("china_customs",), 0,
                                min_rank=2, origin="table", source="china_customs")
        return {c: _round(l.value) for c, l in sorted(legs.items())}
    if src not in ("cbr", "cfets"):
        raise ValueError(f"источник «{source}» не скачивается: допустимо cbr, cfets, china_customs")
    if settings.get("offline"):
        raise RateUnavailable("включён офлайн-режим (config/rates.yaml → offline: true)")
    _FAILS.pop((src, d.isoformat()), None)
    _DOWN.pop(src, None)
    rec = _fetch(src, d, settings, user=user, force=True)
    return _record_rates(rec)


def _row(**kw) -> dict:
    row = {"source": "", "source_title": "", "base": "", "quote": "", "value": None,
           "date": "", "effective_date": "", "manual": False, "note": "", "id": "",
           "scope": "", "comment": "", "user": ""}
    row.update(kw)
    row["source_title"] = _title(row["source"])
    return row


def list_rates(on, source: str | None = None) -> list[dict]:
    """Строки для таблицы интерфейса на дату: официальные курсы + ручные записи.

    Курсы cbr — «валюта → RUB», cfets и china_customs — «валюта → CNY».
    Если источник недоступен, вместо курсов — одна строка с value=None и причиной в note.
    """
    d = _norm_date(on)
    ds = d.isoformat()
    settings = load_settings()
    entries = storage.manual_load()
    wanted = [str(source).strip().lower()] if source else ["cbr", "cfets", "china_customs", "manual"]
    for w in wanted:
        if w not in SOURCES:
            raise ValueError(f"неизвестный источник «{source}»")
    rows: list[dict] = []
    for src in ("cbr", "cfets"):
        if src not in wanted:
            continue
        try:
            rec = _official_record(src, d, settings)
        except RateUnavailable as exc:
            rows.append(_row(source=src, date=ds, note=f"недоступно: {exc}"))
            continue
        eff = str(rec.get("effective_date") or ds)
        quoted = rec.get("_quoted") or {}
        for c, v in sorted(_record_rates(rec).items()):
            note = []
            if c in quoted:
                note.append(f"котировка источника: {quoted[c]}")
            if rec.get("_provisional"):
                note.append("курс на дату ещё не опубликован")
            rows.append(_row(source=src, base=c, quote=PIVOT[src], value=_round(v), date=ds,
                             effective_date=eff, note="; ".join(note)))
    max_back = settings["max_back_days"]
    for e in sorted(entries, key=lambda x: (x.get("base", ""), x.get("quote", ""))):
        scope = e.get("source_scope", "any")
        is_table = scope == "china_customs"
        src = "china_customs" if is_table else "manual"
        if src not in wanted:
            continue
        rank = _entry_rank(e, d, 0 if is_table else max_back)
        if rank < (2 if is_table else 1):
            continue
        note = ("таблица таможни КНР" if is_table else f"ручной, область: {scope}")
        if rank == 1:
            note += f"; запись от {e.get('date')} (ближайшая раньше даты)"
        rows.append(_row(source=src, base=e.get("base", ""), quote=e.get("quote", ""),
                         value=e.get("value"), date=ds, effective_date=_entry_eff(e),
                         manual=True, note=note, id=e.get("id", ""), scope=scope,
                         comment=e.get("comment", ""), user=e.get("user", "")))
    return rows


def set_manual(base, quote, value, *, date=None, month=None, source_scope: str = "any",
               comment: str = "", user: str = "-") -> dict:
    """Добавить ручной курс «1 base = value quote» на дату (date) или на месяц (month).

    source_scope: any | cbr | cfets | china_customs (china_customs — это и есть таблица
    месячных курсов таможни КНР). Запись с теми же областью, парой и датой/месяцем
    заменяется (id сохраняется). Возвращает запись.
    """
    b = norm_currency(base)
    q = norm_currency(quote)
    if not is_iso(b) or not is_iso(q):
        raise ValueError(f"непонятная валюта: «{base}» / «{quote}»")
    if b == q:
        raise ValueError("валюты курса совпадают")
    try:
        v = _to_float(value)
    except (TypeError, ValueError):
        raise ValueError(f"курс должен быть числом, получено «{value}»") from None
    if not v > 0 or v != v or v == float("inf"):
        raise ValueError("курс должен быть больше нуля")
    if (date in (None, "")) == (month in (None, "")):
        raise ValueError("укажите либо дату (date), либо месяц (month)")
    day = _norm_date(date).isoformat() if date not in (None, "") else None
    mon = _norm_month(month) if month not in (None, "") else None
    scope = str(source_scope or "any").strip().lower()
    if scope not in SCOPES:
        raise ValueError(f"source_scope: допустимо {' | '.join(SCOPES)}")
    rec = {"id": uuid.uuid4().hex[:12], "source_scope": scope, "base": b, "quote": q,
           "date": day, "month": mon, "value": v, "comment": str(comment or "")[:300],
           "user": user or "-", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    replaced = None
    with storage.LOCK:
        entries = storage.manual_load()
        for i, e in enumerate(entries):
            if (e.get("source_scope"), e.get("base"), e.get("quote"), e.get("date"),
                    e.get("month")) == (scope, b, q, day, mon):
                replaced = e
                rec["id"] = e.get("id") or rec["id"]
                entries[i] = rec
                break
        else:
            entries.append(rec)
        storage.manual_save(entries)
    when = day or f"месяц {mon}"
    text = f"Ручной курс 1 {b} = {v:g} {q} на {when} (область: {scope})"
    if replaced:
        text += f", заменён прежний {replaced.get('value')}"
    if rec["comment"]:
        text += f". {rec['comment']}"
    log("rate_manual_add", user, text,
        meta={"id": rec["id"], "base": b, "quote": q, "value": v, "date": day, "month": mon,
              "source_scope": scope, "replaced": replaced.get("value") if replaced else None})
    return dict(rec)


def delete_manual(entry_id: str, user: str = "-") -> bool:
    """Удалить ручную запись по id. False — такой записи нет."""
    with storage.LOCK:
        entries = storage.manual_load()
        keep = [e for e in entries if e.get("id") != entry_id]
        if len(keep) == len(entries):
            return False
        gone = next(e for e in entries if e.get("id") == entry_id)
        storage.manual_save(keep)
    when = gone.get("date") or f"месяц {gone.get('month')}"
    log("rate_manual_del", user,
        f"Удалён ручной курс 1 {gone.get('base')} = {gone.get('value')} {gone.get('quote')} "
        f"на {when} (область: {gone.get('source_scope')})",
        meta={"id": entry_id, "entry": gone})
    return True


def list_manual() -> list[dict]:
    """Все ручные записи: свежие даты/месяцы первыми."""
    entries = storage.manual_load()
    return sorted(entries, key=lambda e: (e.get("date") or e.get("month") or "", e.get("ts", "")),
                  reverse=True)


def load_settings() -> dict:
    """Настройки config/rates.yaml (mode, sides, max_back_days, http_timeout, offline).

    Ошибочные значения в файле заменяются значениями по умолчанию.
    """
    raw = storage.settings_load()
    try:
        return storage.validate_settings(raw)
    except ValueError:
        good: dict = {}
        for k, v in raw.items():
            try:
                storage.validate_settings({**good, k: v})
                good[k] = v
            except ValueError:
                continue
        return storage.validate_settings(good)


def save_settings(d: dict, user: str = "-") -> dict:
    """Сохранить настройки (можно частично: {"mode": "manual_override"}). Возвращает итог."""
    if not isinstance(d, dict):
        raise ValueError("настройки — словарь")
    old = load_settings()
    new = storage._merge(old, {k: v for k, v in d.items() if not str(k).startswith("_")})
    clean = storage.settings_save(new)
    changes = {}
    for k in sorted(set(old) | set(clean)):
        if k == "sides":
            for s in sorted(set(old.get("sides", {})) | set(clean.get("sides", {}))):
                a, b = old.get("sides", {}).get(s), clean.get("sides", {}).get(s)
                if a != b:
                    changes[f"sides.{s}"] = [a, b]
        elif old.get(k) != clean.get(k):
            changes[k] = [old.get(k), clean.get(k)]
    text = ("Настройки курсов: " + "; ".join(f"{k}: {a} → {b}" for k, (a, b) in changes.items())
            if changes else "Настройки курсов сохранены без изменений")
    log("rate_settings", user, text, meta={"changes": changes})
    return clean
