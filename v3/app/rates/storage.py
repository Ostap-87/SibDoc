# -*- coding: utf-8 -*-
"""Хранилище модуля курсов: кэш официальных курсов, ручные курсы, настройки.

Файлы:
- storage/rates/cache.json  — {источник: {дата: {валюта: значение, "effective_date": ...}}};
- storage/rates/manual.json — {"entries": [ручные записи]};
- config/rates.yaml         — режим и параметры (с комментариями; при сохранении
  комментарии сохраняются — файл пересобирается из шаблона).

Запись атомарная: временный файл в той же папке + os.replace.
Пути читаются из переменных модуля при каждом вызове — тесты подменяют
RATES_DIR и CONFIG_FILE через monkeypatch.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading

import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RATES_DIR = os.path.join(PROJECT_ROOT, "storage", "rates")
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config", "rates.yaml")

LOCK = threading.RLock()          # чтение-изменение-запись файлов модуля

MODES = ("official", "manual_override")
OFFICIAL_SOURCES = ("cbr", "cfets", "china_customs")

DEFAULT_SETTINGS: dict = {
    "mode": "official",
    "sides": {"ru": "cbr", "cn": "cfets", "cn_customs": "china_customs"},
    "max_back_days": 14,
    "http_timeout": 15,
    "offline": False,
}

_SETTINGS_TEMPLATE = """\
# ================================================================
#  Курсы валют (модуль app/rates)
#  Файл можно править руками или из интерфейса (save_settings).
#  При сохранении из интерфейса файл пересобирается, комментарии остаются.
# ================================================================

# Режим выбора курса:
#   official        — по умолчанию: берём официальный курс (ЦБ РФ, ЦБ КНР/CFETS,
#                     таблица таможни КНР). Ручной курс используется только если
#                     официальный недоступен, с пометкой «ручной: официальный недоступен».
#   manual_override — ручная запись на дату (или на месяц) важнее официальной.
mode: {mode}

# Какой источник для какой стороны сделки:
#   ru         — российская сторона (ДТ, SAP): cbr = ЦБ РФ, рубли за единицу валюты;
#   cn         — китайская сторона (报关单, контракты): cfets = центральный курс
#                НБК / CFETS (中间价), юани за единицу валюты;
#   cn_customs — китайская таможенная стоимость: china_customs = месячный курс
#                таможни КНР (海关计征汇率) из ручной таблицы по месяцу.
# Допустимые значения: cbr | cfets | china_customs.
# Например, чтобы вся китайская сторона считалась по курсу таможни: cn: china_customs
sides:
  ru: {side_ru}
  cn: {side_cn}
  cn_customs: {side_cn_customs}

# Сколько дней назад можно взять последний опубликованный курс, если на саму дату
# курса нет (выходные, праздники). ЦБ РФ в новогодние праздники держит курс от 31.12
# почти до середины января (до 12 дней), CFETS не публикует курс в праздники КНР
# (Праздник весны, неделя 1–7 октября), поэтому по умолчанию 14.
max_back_days: {max_back_days}

# Таймаут одного запроса к сайту ЦБ РФ / chinamoney.com.cn, секунд.
http_timeout: {http_timeout}

# true — не ходить в интернет: только сохранённый кэш и ручные курсы
# (например, сервер в закрытом контуре без доступа наружу).
offline: {offline}
"""


# --------------------------------------------------------------------- файлы
def cache_file() -> str:
    return os.path.join(RATES_DIR, "cache.json")


def manual_file() -> str:
    return os.path.join(RATES_DIR, "manual.json")


def write_atomic(path: str, text: str) -> None:
    """Записать файл целиком: tmp в той же папке + os.replace (без «половинок»)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)              # mkstemp создаёт 0600 — делаем как у обычных файлов
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_MEMO: dict[str, tuple[tuple, object]] = {}


def _stamp(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read_json(path: str, default, *, shared: bool = False):
    """JSON с диска; повторное чтение без изменений файла берётся из памяти.

    shared=True — вернуть объект из памяти без копии (только для чтения!):
    кэш курсов может быть большим, а get_rate вызывается на каждую строку.
    """
    stamp = _stamp(path)
    if stamp is None:
        return copy.deepcopy(default)
    memo = _MEMO.get(path)
    if not (memo and memo[0] == stamp):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return copy.deepcopy(default)
        memo = (stamp, data)
        _MEMO[path] = memo
    return memo[1] if shared else copy.deepcopy(memo[1])


def _write_json(path: str, data) -> None:
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
    _MEMO.pop(path, None)


# --------------------------------------------------------------------- кэш
def cache_load() -> dict:
    data = _read_json(cache_file(), {})
    return data if isinstance(data, dict) else {}


def cache_get(source: str, day: str) -> dict | None:
    """Запись кэша {валюта: значение, effective_date, ...} или None (копия записи)."""
    data = _read_json(cache_file(), {}, shared=True)
    rec = data.get(source, {}).get(day) if isinstance(data, dict) else None
    return copy.deepcopy(rec) if isinstance(rec, dict) else None


def cache_put(source: str, records: dict[str, dict]) -> None:
    """Добавить/заменить записи кэша источника: {дата: запись}."""
    if not records:
        return
    with LOCK:
        data = cache_load()
        data.setdefault(source, {}).update(records)
        _write_json(cache_file(), data)


# --------------------------------------------------------------------- ручные
def manual_load() -> list[dict]:
    data = _read_json(manual_file(), {"entries": []})
    entries = data.get("entries", []) if isinstance(data, dict) else []
    return [e for e in entries if isinstance(e, dict)]


def manual_save(entries: list[dict]) -> None:
    with LOCK:
        _write_json(manual_file(), {"entries": entries})


# --------------------------------------------------------------------- настройки
def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def settings_load() -> dict:
    """Настройки из config/rates.yaml поверх значений по умолчанию."""
    stamp = _stamp(CONFIG_FILE)
    raw: dict = {}
    if stamp is not None:
        memo = _MEMO.get(CONFIG_FILE)
        if memo and memo[0] == stamp:
            raw = copy.deepcopy(memo[1])
        else:
            try:
                with open(CONFIG_FILE, encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            _MEMO[CONFIG_FILE] = (stamp, copy.deepcopy(raw))
    return _merge(DEFAULT_SETTINGS, raw)


def validate_settings(s: dict) -> dict:
    """Проверить и привести типы; ValueError с понятным текстом при ошибке."""
    out = _merge(DEFAULT_SETTINGS, s)
    out = {k: out[k] for k in DEFAULT_SETTINGS}          # неизвестные ключи не храним
    mode = str(out.get("mode", "")).strip()
    if mode not in MODES:
        raise ValueError(f"mode: допустимо {' | '.join(MODES)}, получено «{mode}»")
    out["mode"] = mode
    sides = out.get("sides")
    if not isinstance(sides, dict):
        raise ValueError("sides: нужен словарь вида {ru: cbr, cn: cfets, cn_customs: china_customs}")
    clean_sides = {}
    for side, src in sides.items():
        src = str(src).strip().lower()
        if src not in OFFICIAL_SOURCES:
            raise ValueError(f"sides.{side}: допустимо {' | '.join(OFFICIAL_SOURCES)}, получено «{src}»")
        clean_sides[str(side).strip().lower()] = src
    out["sides"] = clean_sides
    try:
        out["max_back_days"] = int(out["max_back_days"])
        out["http_timeout"] = float(out["http_timeout"])
    except (TypeError, ValueError):
        raise ValueError("max_back_days и http_timeout должны быть числами") from None
    if not 0 <= out["max_back_days"] <= 60:
        raise ValueError("max_back_days: от 0 до 60 дней")
    if not 1 <= out["http_timeout"] <= 120:
        raise ValueError("http_timeout: от 1 до 120 секунд")
    if out["http_timeout"].is_integer():
        out["http_timeout"] = int(out["http_timeout"])
    off = out.get("offline")
    if isinstance(off, str):
        off = off.strip().lower() in ("1", "true", "yes", "да", "on")
    out["offline"] = bool(off)
    return out


def render_settings(s: dict) -> str:
    """Текст config/rates.yaml с комментариями (стандартные стороны — в шаблоне)."""
    sides = dict(s["sides"])
    text = _SETTINGS_TEMPLATE.format(
        mode=s["mode"],
        side_ru=sides.pop("ru", DEFAULT_SETTINGS["sides"]["ru"]),
        side_cn=sides.pop("cn", DEFAULT_SETTINGS["sides"]["cn"]),
        side_cn_customs=sides.pop("cn_customs", DEFAULT_SETTINGS["sides"]["cn_customs"]),
        max_back_days=s["max_back_days"],
        http_timeout=s["http_timeout"],
        offline="true" if s["offline"] else "false",
    )
    if sides:   # дополнительные стороны, добавленные пользователем
        extra = "".join(f"  {k}: {v}\n" for k, v in sides.items())
        text = text.replace("  cn_customs: ", extra + "  cn_customs: ", 1)
    return text


def settings_save(s: dict) -> dict:
    clean = validate_settings(s)
    with LOCK:
        write_atomic(CONFIG_FILE, render_settings(clean))
        _MEMO.pop(CONFIG_FILE, None)
    return clean
