# -*- coding: utf-8 -*-
"""Базовый класс коннектора источников данных.

Каждый коннектор умеет:
  test(user)             — проверить доступность/доступ (без загрузки данных)
  pull(dest_dir, user)   — забрать свежие файлы данных в dest_dir
  public_dict()          — описание для интерфейса (без секретов)

Наследник объявляет атрибуты класса и реализует _test() / _pull(dest_dir);
обёртки test()/pull() пишут журнал (app.journal: connector_test, connector_pull,
connector_error), вычищают секреты из текстов ошибок и не дают запустить «задел»
(stage = "needs_access") без заполненных параметров доступа.

  type   = "imap"                     # ключ в config/system.yaml -> connectors.<id>.type
  title  = "Почта (IMAP)"             # по-русски, для интерфейса
  stage  = "ready"                    # ready — рабочий; needs_access — задел до получения доступа
  params = [P("host", "Сервер", required=True, example="imap.company.ru"),
            P("password", "Пароль", required=True, secret=True,
              example="password_env: SIBDOC_IMAP_PASSWORD"), ...]

Секреты (см. secrets.py): значение берётся из переменной окружения, имя которой
в cfg["<key>_env"]; поддерживается и ${VAR} в строке. Литеральный пароль в YAML
работает, но public_dict() его никогда не отдаёт и показывает предупреждение.

Слоты. Файл попадает в слот:
  1) по правилам slot_rules (regex по имени файла; для почты — ещё и по отправителю):
       slot_rules:
         - {pattern: '(?i)(报关单|customs.?dec|cn_dec)', slot: cn_dec}
         - {pattern: '(?i)(\\bДТ\\b|ESADout|gtd|ru_dt)', slot: ru_dt}
         - {pattern: '(?i)\\.xml$', sender: '(?i)@broker-ru\\.example$', slot: ru_dt}
     (\\b не срабатывает между «ДТ» и «_»: для имён вида «ДТ_107...xml» надёжнее
      '(?<![A-Za-zА-Яа-я])ДТ(?![A-Za-zА-Яа-я])');
  2) по началу имени из списка slots: 'wide_table_2026-10-06.xlsx' -> wide_table.
Слот назначается только табличным/XML-файлам (.xlsx .xls .csv .xml); PDF и прочее
сохраняются без слота. Если имя не начинается со слота, файл сохраняется как
«<slot>_<исходное имя>» (prefix_slot: false — отключить): сервер раскладывает
файлы по слотам именно по началу имени.
Известные слоты: wide_table, purchase, sap_vs_cd, cd_drafts, hs_reference,
cde_reference, ru_dt (российская ДТ: XML ФТС или Excel), cn_dec (китайская
декларация 报关单: Excel/CSV/XML брокера).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone

from . import fileutil
from . import secrets as _sec

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# состояние коннекторов (что уже забрано): storage/connectors/<id>.seen.json
STATE_DIR = os.path.join(PROJECT_ROOT, "storage", "connectors")

KNOWN_SLOTS = ("wide_table", "purchase", "sap_vs_cd", "cd_drafts", "hs_reference",
               "cde_reference", "ru_dt", "cn_dec")

STAGE_READY = "ready"
STAGE_NEEDS_ACCESS = "needs_access"
STAGE_TITLES = {STAGE_READY: "рабочий", STAGE_NEEDS_ACCESS: "задел: ждёт доступа"}

DEFAULT_SLOT_RULES = [
    {"pattern": r"(?i)(报关单|customs.?dec|cn_dec)", "slot": "cn_dec"},
    {"pattern": r"(?i)((?<![a-zа-яё])ДТ(?![a-zа-яё])|ESADout|gtd|ru_dt)", "slot": "ru_dt"},
]


def P(key: str, label: str, required: bool = False, secret: bool = False,
      example: str = "", **extra) -> dict:
    """Описание поля коннектора для интерфейса."""
    d = {"key": key, "label": label, "required": bool(required),
         "secret": bool(secret), "example": example}
    d.update(extra)
    return d


SLOT_PARAMS = [
    P("slots", "Слоты по началу имени файла", example="[wide_table, purchase]"),
    P("slot_rules", "Правила слотов (regex по имени файла)",
      example="[{pattern: '(?i)(报关单|customs.?dec)', slot: cn_dec}]"),
    P("prefix_slot", "Добавлять слот в начало имени файла", example="true"),
]
TLS_PARAMS = [
    P("verify_tls", "Проверять сертификат сервера", example="true"),
    P("ca_bundle", "Свой корневой сертификат (PEM)", example="/etc/ssl/corp-ca.pem"),
]


class ConnectorError(Exception):
    pass


def _journal(kind: str, user: str, detail: str, meta: dict | None = None) -> None:
    """Запись в журнал; ошибка журнала не должна ронять коннектор."""
    try:
        from app.journal import log
        log(kind, user or "system", detail, meta)
    except Exception:  # pragma: no cover
        pass


class Connector:
    type = "base"
    title = "Коннектор"
    stage = STAGE_READY
    params: list[dict] = []
    # для stage = needs_access: что получить и у кого
    access_hint = ""

    def __init__(self, cid: str, cfg: dict):
        self.id = cid
        self.cfg = dict(cfg or {})
        self.enabled = _sec.to_bool(self.cfg.get("enabled"), False)
        self.label = self.cfg.get("label") or cid
        self.last_notes: list[str] = []

    # ================================================================ config
    def opt(self, key: str, default=None):
        """Несекретный параметр с подстановкой ${VAR}."""
        v = self.cfg.get(key)
        if v is None or v == "":
            return default
        return _sec.expand_env(v)

    def opt_bool(self, key: str, default: bool = False) -> bool:
        return _sec.to_bool(self.opt(key), default)

    def opt_int(self, key: str, default: int) -> int:
        try:
            return int(self.opt(key, default))
        except (TypeError, ValueError):
            return default

    def opt_list(self, key: str) -> list:
        v = self.opt(key)
        if v in (None, ""):
            return []
        if isinstance(v, (list, tuple)):
            return [x for x in v if x not in (None, "")]
        return [v]

    def secret(self, key: str) -> str:
        """Секрет: из переменной окружения cfg[key + '_env'] или ${VAR} в cfg[key]."""
        return _sec.resolve_secret(self.cfg, key)

    def _secret_values(self) -> list[str]:
        vals = []
        keys = {p["key"] for p in self.params if p.get("secret")}
        keys |= {k for k in self.cfg if _sec.SENSITIVE_KEY.search(str(k))
                 and not str(k).endswith(("_env", "_url", "_header", "_path", "_file"))}
        for k in keys:
            v = self.secret(k)
            if v:
                vals.append(v)
        for v in self.cfg.values():
            if isinstance(v, dict):
                for hk, hv in v.items():
                    if _sec.SENSITIVE_KEY.search(str(hk)) and hv:
                        vals.append(str(_sec.expand_env(hv)))
        return vals

    def redact(self, text) -> str:
        return _sec.redact(text, self._secret_values())

    # ================================================================ describe
    def _param_set(self, p: dict) -> bool:
        if p.get("secret"):
            return bool(self.secret(p["key"]))
        return self.opt(p["key"]) not in (None, "", [], {})

    def _extra_missing(self) -> list[str]:
        """Условно обязательные поля (например, пароль только при auth: basic)."""
        return []

    def missing(self) -> list[str]:
        out = [p["key"] for p in self.params if p.get("required") and not self._param_set(p)]
        for k in self._extra_missing():
            if k not in out:
                out.append(k)
        return out

    def warnings(self) -> list[str]:
        out = []
        for p in self.params:
            if p.get("secret") and _sec.secret_source(self.cfg, p["key"]) == "literal":
                out.append(f"{p['key']}: секрет записан в YAML открытым текстом — "
                           f"перенесите в переменную окружения ({p['key']}_env)")
            if p.get("secret") and self.cfg.get(p["key"] + "_env") and not self.secret(p["key"]):
                out.append(f"{p['key']}: переменная окружения "
                           f"{self.cfg.get(p['key'] + '_env')} не задана на сервере")
        if self.cfg.get("verify_tls") is not None and not self.opt_bool("verify_tls", True):
            out.append("verify_tls: false — сертификат сервера не проверяется")
        for rule in self.cfg.get("slot_rules") or []:
            try:
                re.compile(str((rule or {}).get("pattern", "")))
            except re.error as exc:
                out.append(f"slot_rules: ошибка в регулярном выражении {rule!r}: {exc}")
        return out

    def describe_params(self) -> list[dict]:
        out = []
        for p in self.params:
            d = {k: p.get(k) for k in ("key", "label", "required", "secret", "example")}
            d["set"] = self._param_set(p)
            if p.get("secret"):
                d["source"] = _sec.secret_source(self.cfg, p["key"])
                env = self.cfg.get(p["key"] + "_env")
                if env:
                    d["env"] = str(env)
            else:
                raw = self.cfg.get(p["key"])
                if raw is not None:
                    d["value"] = _sec.mask_value(raw)
            out.append(d)
        return out

    def public_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "title": self.title,
                "label": self.label, "enabled": self.enabled, "stage": self.stage,
                "stage_title": STAGE_TITLES.get(self.stage, self.stage),
                "params": self.describe_params(), "missing": self.missing(),
                "warnings": self.warnings()}

    @classmethod
    def catalog_entry(cls) -> dict:
        return {"type": cls.type, "title": cls.title, "stage": cls.stage,
                "stage_title": STAGE_TITLES.get(cls.stage, cls.stage),
                "access_hint": cls.access_hint,
                "params": [dict(p) for p in cls.params]}

    # ================================================================ api
    def _gate(self) -> str | None:
        """Текст отказа, если запускать рано (не заполнены поля / нет доступа)."""
        secret_keys = {p["key"] for p in self.params if p.get("secret")}
        miss = [f"{k}_env" if k in secret_keys else k for k in self.missing()]
        if not miss:
            return None
        if self.stage == STAGE_NEEDS_ACCESS:
            return (f"Задел: нужен доступ — {self.access_hint}"
                    f" (не заполнено: {', '.join(miss)})")
        return f"не заполнены обязательные поля: {', '.join(miss)}"

    def test(self, user: str = "system") -> dict:
        """-> {"ok": bool, "detail": str}"""
        gate = self._gate()
        if gate:
            res = {"ok": False, "detail": gate}
        else:
            try:
                res = dict(self._test() or {})
            except ConnectorError as exc:
                res = {"ok": False, "detail": str(exc)}
            except Exception as exc:
                res = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        res["ok"] = bool(res.get("ok"))
        res["detail"] = self.redact(res.get("detail", ""))[:500]
        if res.get("warnings"):
            res["warnings"] = [self.redact(w) for w in res["warnings"]]
        _journal("connector_test", user,
                 f"{self.id} ({self.title}): {'доступ есть' if res['ok'] else 'нет доступа'} — "
                 f"{res['detail']}",
                 {"connector": self.id, "type": self.type, "ok": res["ok"]})
        return res

    def pull(self, dest_dir: str, user: str = "system") -> list[dict]:
        """-> [{"slot": str|None, "file": исходное имя, "path": abs_path, "rows": int|None}]"""
        self.last_notes = []
        gate = self._gate()
        try:
            if gate:
                raise ConnectorError(gate)
            items = list(self._pull(dest_dir) or [])
        except Exception as exc:
            msg = self.redact(str(exc) if isinstance(exc, ConnectorError)
                              else f"{type(exc).__name__}: {exc}")[:500]
            _journal("connector_error", user, f"{self.id} ({self.title}): ошибка загрузки — {msg}",
                     {"connector": self.id, "type": self.type})
            raise ConnectorError(msg) from exc
        slots = sorted({i["slot"] for i in items if i.get("slot")})
        detail = (f"{self.id} ({self.title}): получено файлов {len(items)}"
                  + (f", слоты: {', '.join(slots)}" if slots else "")
                  + (f"; пропущено: {len(self.last_notes)}" if self.last_notes else ""))
        meta = {"connector": self.id, "type": self.type,
                "files": [i.get("file") for i in items][:50]}
        if self.last_notes:
            meta["notes"] = [self.redact(n) for n in self.last_notes][:50]
        _journal("connector_pull", user, detail, meta)
        return items

    def _test(self) -> dict:
        raise NotImplementedError

    def _pull(self, dest_dir: str) -> list[dict]:
        raise NotImplementedError

    # ================================================================ slots
    @staticmethod
    def _slot_for_name(name: str, slots: list[str]) -> str | None:
        """Слот по имени файла: 'wide_table_2026-10-06.xlsx' -> 'wide_table'."""
        stem = os.path.splitext(os.path.basename(name))[0].lower()
        for s in slots or []:
            s = str(s)
            if stem == s.lower() or stem.startswith(s.lower() + "_") \
               or stem.startswith(s.lower() + "-") or stem.startswith(s.lower() + " "):
                return s
        return None

    def _compiled_rules(self) -> list[tuple]:
        out = []
        for rule in self.cfg.get("slot_rules") or []:
            if not isinstance(rule, dict) or not rule.get("slot") or not rule.get("pattern"):
                continue
            try:
                rx = re.compile(str(rule["pattern"]))
                srx = re.compile(str(rule["sender"])) if rule.get("sender") else None
            except re.error:
                continue
            out.append((rx, str(rule["slot"]), srx))
        return out

    def slot_for(self, name: str, sender: str | None = None) -> str | None:
        """Слот файла: сначала slot_rules, затем префикс из slots."""
        base = os.path.basename(name)
        exts = tuple(e.lower() for e in self.opt_list("slot_exts")) or fileutil.SLOT_EXTS
        if not base.lower().endswith(exts):
            return None
        for rx, slot, srx in self._compiled_rules():
            if not rx.search(base):
                continue
            if srx is not None and not (sender and srx.search(sender)):
                continue
            return slot
        return self._slot_for_name(base, self.opt_list("slots"))

    # ================================================================ files
    def _store_file(self, dest_dir: str, name: str, data: bytes, *, sender: str | None = None,
                    masks=None, origin: str | None = None, used: set | None = None) -> list[dict]:
        """Сохранить полученный файл; zip — распаковать и взять файлы по маскам.

        Пропущенное (небезопасные пути, zip-бомбы и т.п.) — в self.last_notes."""
        name = fileutil.safe_filename(name)
        if name.lower().endswith(".zip") and self.opt_bool("unzip", True):
            try:
                entries, notes = fileutil.extract_zip(
                    data,
                    max_total=self.opt_int("zip_max_total_mb", 200) * 2**20,
                    max_file=self.opt_int("zip_max_file_mb", 100) * 2**20,
                    max_files=self.opt_int("zip_max_files", 1000))
            except ValueError as exc:
                self.last_notes.append(f"{name}: {exc}")
                return []
            self.last_notes.extend(f"{name}: {n}" for n in notes)
            out = []
            for ename, edata in entries:
                if masks and not fileutil.match_masks(ename, masks):
                    continue
                if ename.lower().endswith(".zip"):
                    continue
                out.extend(self._save_one(dest_dir, ename, edata, sender=sender,
                                          origin=f"{origin + ' / ' if origin else ''}{name}",
                                          used=used))
            if not out:
                self.last_notes.append(f"{name}: в архиве нет подходящих файлов")
            return out
        return self._save_one(dest_dir, name, data, sender=sender, origin=origin, used=used)

    def _save_one(self, dest_dir: str, name: str, data: bytes, *, sender: str | None = None,
                  origin: str | None = None, used: set | None = None) -> list[dict]:
        slot = self.slot_for(name, sender)
        item: dict = {"slot": slot, "file": name}
        if name.lower().endswith(".csv") and self.opt_bool("csv_to_utf8", True):
            data, enc = fileutil.csv_to_utf8(data)
            if enc not in ("utf-8", "utf-8-sig"):
                item["encoding"] = enc
        out_name = name
        if slot and self.opt_bool("prefix_slot", True) and self._slot_for_name(name, [slot]) is None:
            out_name = f"{slot}_{name}"
        out_name = fileutil.unique_name(out_name, used)
        item["path"] = self._write_bytes(dest_dir, out_name, data)
        item["rows"] = None
        if out_name != name:
            item["saved_as"] = out_name
        if origin:
            item["origin"] = origin
        return [item]

    @staticmethod
    def _write_bytes(dest_dir: str, filename: str, content: bytes) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        # защита от path traversal
        safe = fileutil.safe_filename(os.path.basename(filename))
        path = os.path.join(dest_dir, safe)
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    # ================================================================ records -> csv
    @staticmethod
    def _normalize_record(rec, columns: list[str] | None = None) -> dict:
        """Запись API -> плоский словарь: служебные ключи OData убираются,
        вложенные объекты — JSON-строкой, /Date(ms)/ -> ISO-дата."""
        if isinstance(rec, (list, tuple)):
            cols = list(columns or [])
            cols += [f"col_{i + 1}" for i in range(len(cols), len(rec))]
            rec = dict(zip(cols, rec))
        elif not isinstance(rec, dict):
            return {"value": rec}
        out = {}
        for k, v in rec.items():
            k = str(k)
            if k.startswith("__") or k.startswith("@odata.") or k.startswith("odata."):
                continue
            if isinstance(v, dict):
                if set(v.keys()) <= {"__deferred"}:
                    continue
                v = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, list):
                v = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, str):
                m = re.fullmatch(r"/Date\((-?\d+)([+-]\d{4})?\)/", v)
                if m:
                    dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
                    v = dt.strftime("%Y-%m-%d") if dt.strftime("%H%M%S") == "000000" \
                        else dt.strftime("%Y-%m-%d %H:%M:%S")
            out[k] = v
        return out

    def _write_records_csv(self, dest_dir: str, fname: str, records: list,
                           columns: list[str] | None = None) -> tuple[str, int]:
        rows = [self._normalize_record(r, columns) for r in records]
        cols: list[str] = []
        seen = set()
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, fileutil.safe_filename(fname))
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if v is None else v) for k, v in r.items()})
        return path, len(rows)

    # ================================================================ http
    @staticmethod
    def _requests():
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("пакет requests не установлен (pip install requests)") from exc
        return requests

    def _verify(self):
        ca = self.opt("ca_bundle")
        if ca:
            return os.path.expanduser(str(ca))
        return self.opt_bool("verify_tls", True)


class SeenStore:
    """Что уже забрано: storage/connectors/<id>.seen.json -> {ключ: время}."""

    LIMIT = 20000

    def __init__(self, cid: str, state_dir: str | None = None):
        safe = re.sub(r"[^\w.-]", "_", cid)
        self.path = os.path.join(state_dir or STATE_DIR, f"{safe}.seen.json")
        self.data: dict[str, str] = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self.data = {str(k): str(v) for k, v in loaded.items()}
            except (OSError, ValueError):
                self.data = {}
        self._dirty = False

    @staticmethod
    def key(*parts) -> str:
        raw = "|".join(str(p) for p in parts)
        return raw if len(raw) <= 300 else "sha1:" + hashlib.sha1(raw.encode()).hexdigest()

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def add(self, key: str) -> None:
        self.data[key] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        if len(self.data) > self.LIMIT:
            items = sorted(self.data.items(), key=lambda kv: kv[1])[-self.LIMIT:]
            self.data = dict(items)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, self.path)
        self._dirty = False
