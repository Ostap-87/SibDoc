# -*- coding: utf-8 -*-
"""Журнал событий: загрузки, проверки, коннекторы, входы, пользователи.

Хранение: storage/journal.jsonl (одна запись = одна строка JSON).
"""
from __future__ import annotations

import json
import os
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
JOURNAL_FILE = os.path.join(PROJECT_ROOT, "storage", "journal.jsonl")

KINDS = ("upload", "delete", "run", "pull", "reload", "login", "logout",
         "user_add", "user_del", "user_pw", "ocr")


def log(kind: str, user: str, detail: str, meta: dict | None = None) -> dict:
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
           "user": user or "-", "detail": detail[:500]}
    if meta:
        rec["meta"] = meta
    os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
    with open(JOURNAL_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _iter_records():
    if not os.path.exists(JOURNAL_FILE):
        return
    with open(JOURNAL_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def query(day: str | None = None, q: str | None = None, limit: int = 500) -> list[dict]:
    """Фильтр по дню (YYYY-MM-DD) и/или подстроке; свежие первыми."""
    needle = (q or "").strip().lower()
    out = []
    for rec in _iter_records():
        if day and not rec.get("ts", "").startswith(day):
            continue
        if needle:
            hay = " ".join([rec.get("kind", ""), rec.get("user", ""),
                            rec.get("detail", ""),
                            json.dumps(rec.get("meta", {}), ensure_ascii=False)]).lower()
            if needle not in hay:
                continue
        out.append(rec)
    out.reverse()
    return out[:limit]


def days_in_month(month: str) -> dict[str, int]:
    """-> {'2026-10-01': 5, ...} — число событий по дням месяца (YYYY-MM)."""
    out: dict[str, int] = {}
    prefix = (month or "")[:7]
    for rec in _iter_records():
        ts = rec.get("ts", "")
        if prefix and not ts.startswith(prefix):
            continue
        day = ts[:10]
        out[day] = out.get(day, 0) + 1
    return out
