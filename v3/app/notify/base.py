# -*- coding: utf-8 -*-
"""Базовый класс канала уведомлений (WeCom, Telegram, почта).

Секреты — как у коннекторов: из переменной окружения (<key>_env) или ${VAR};
литеральные значения работают, но наружу (public_dict, журнал) не выдаются.
"""
from __future__ import annotations

import os

from app.connectors import secrets as _sec

EVENTS = {
    "run_done": "прогон завершён",
    "issues_found": "найдены расхождения",
    "connector_error": "ошибка коннектора",
    "test": "тестовое сообщение",
}


class NotifyError(Exception):
    pass


def P(key: str, label: str, required: bool = False, secret: bool = False, example: str = "") -> dict:
    return {"key": key, "label": label, "required": bool(required), "secret": bool(secret),
            "example": example}


def split_text(text: str, limit: int, unit=len, max_parts: int = 3,
               suffix: str = "\n…(сообщение обрезано)") -> list[str]:
    """Разбить текст на части не длиннее limit (в единицах unit(символ)) по строкам;
    частей не больше max_parts, последняя при необходимости обрезается."""
    def size(s: str) -> int:
        return sum(unit(ch) for ch in s)

    text = text or ""
    if size(text) <= limit:
        return [text]
    parts: list[str] = []
    cur, cur_size = "", 0
    for line in text.splitlines(keepends=True):
        ln = size(line)
        if cur_size + ln <= limit:
            cur += line
            cur_size += ln
            continue
        if cur:
            parts.append(cur)
            cur, cur_size = "", 0
        while ln > limit:                       # строка длиннее лимита — режем по символам
            piece, psize = "", 0
            for i, ch in enumerate(line):
                u = unit(ch)
                if psize + u > limit:
                    line = line[i:]
                    break
                piece += ch
                psize += u
            parts.append(piece)
            ln = size(line)
        cur, cur_size = line, ln
    if cur:
        parts.append(cur)
    if len(parts) > max_parts:
        parts = parts[:max_parts]
        last, budget = "", limit - size(suffix)
        acc = 0
        for ch in parts[-1]:
            u = unit(ch)
            if acc + u > budget:
                break
            last += ch
            acc += u
        parts[-1] = last + suffix
    return parts


def utf8_len(ch: str) -> int:
    return len(ch.encode("utf-8"))


def utf16_len(ch: str) -> int:
    return len(ch.encode("utf-16-le")) // 2


class Channel:
    type = "base"
    title = "Канал"
    params: list[dict] = []

    def __init__(self, cid: str, cfg: dict):
        self.id = cid
        self.cfg = dict(cfg or {})
        self.enabled = _sec.to_bool(self.cfg.get("enabled"), False)
        self.label = self.cfg.get("label") or cid

    # ---------------------------------------------------------------- config
    def opt(self, key: str, default=None):
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
        return [x for x in v if x not in (None, "")] if isinstance(v, (list, tuple)) else [v]

    def secret(self, key: str) -> str:
        return _sec.resolve_secret(self.cfg, key)

    def redact(self, text) -> str:
        vals = [self.secret(p["key"]) for p in self.params if p.get("secret")]
        return _sec.redact(text, [v for v in vals if v])

    def events(self) -> list[str]:
        return [str(e) for e in self.opt_list("events")]

    def wants(self, event: str) -> bool:
        ev = self.events()
        return not ev or "*" in ev or event in ev

    def missing(self) -> list[str]:
        out = []
        for p in self.params:
            if not p.get("required"):
                continue
            ok = bool(self.secret(p["key"])) if p.get("secret") else \
                self.opt(p["key"]) not in (None, "", [], {})
            if not ok:
                out.append(p["key"] + ("_env" if p.get("secret") else ""))
        return out

    def public_dict(self) -> dict:
        params = []
        warnings = []
        for p in self.params:
            d = dict(p)
            if p.get("secret"):
                d["set"] = bool(self.secret(p["key"]))
                d["source"] = _sec.secret_source(self.cfg, p["key"])
                if self.cfg.get(p["key"] + "_env"):
                    d["env"] = str(self.cfg.get(p["key"] + "_env"))
                if d["source"] == "literal":
                    warnings.append(f"{p['key']}: секрет записан в YAML открытым текстом — "
                                    f"перенесите в переменную окружения ({p['key']}_env)")
            else:
                raw = self.cfg.get(p["key"])
                d["set"] = raw not in (None, "", [], {})
                if raw is not None:
                    d["value"] = _sec.mask_value(raw)
            params.append(d)
        return {"id": self.id, "type": self.type, "title": self.title, "label": self.label,
                "enabled": self.enabled, "events": self.events(), "params": params,
                "missing": self.missing(), "warnings": warnings}

    # ---------------------------------------------------------------- send
    def send(self, event: str, text: str, files: list[str] | None = None) -> dict:
        """Отправить; -> {"detail": str, ...}. Ошибка — NotifyError/исключение."""
        raise NotImplementedError

    def _files(self, files: list[str] | None, max_bytes: int) -> tuple[list[str], list[str]]:
        """Отобрать существующие файлы не больше лимита. -> (годные, заметки)."""
        ok, notes = [], []
        for f in files or []:
            if not f or not os.path.isfile(f):
                notes.append(f"файл не найден: {os.path.basename(str(f))}")
                continue
            size = os.path.getsize(f)
            if size > max_bytes:
                notes.append(f"{os.path.basename(f)}: больше {max_bytes // 2**20} МБ, не отправлен")
                continue
            ok.append(f)
        return ok, notes

    @staticmethod
    def _requests():
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise NotifyError("пакет requests не установлен (pip install requests)") from exc
        return requests

    def _verify(self):
        ca = self.opt("ca_bundle")
        if ca:
            return os.path.expanduser(str(ca))
        return self.opt_bool("verify_tls", True)
