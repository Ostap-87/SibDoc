# -*- coding: utf-8 -*-
"""Журнал событий: загрузки, проверки, отчёты, правки правил, курсы, коннекторы,
уведомления, сканы, входы, пользователи.

Хранение: storage/journal.jsonl (одна запись = одна строка JSON, только дописывается).
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
JOURNAL_FILE = os.path.join(PROJECT_ROOT, "storage", "journal.jsonl")

# вид события -> подпись (ru, zh) для интерфейса и выгрузки
KINDS = {
    "upload": ("Загрузка файла", "上传文件"),
    "delete": ("Очистка источника", "清除数据源"),
    "run": ("Проверка", "运行检查"),
    "report": ("Отчёт", "报告"),
    "rules_change": ("Изменение правил", "规则修改"),
    "rules_restore": ("Откат правил", "规则恢复"),
    "reload": ("Обновление данных", "刷新数据"),
    "pull": ("Коннектор: забор данных", "连接器：获取数据"),
    "connector_test": ("Коннектор: проверка", "连接器：测试"),
    "connector_pull": ("Коннектор: забор данных", "连接器：获取数据"),
    "connector_error": ("Коннектор: ошибка", "连接器：错误"),
    "notify": ("Уведомление", "通知"),
    "rate_fetch": ("Курс: загрузка", "汇率：下载"),
    "rate_manual_add": ("Курс: ручной добавлен", "汇率：手动添加"),
    "rate_manual_del": ("Курс: ручной удалён", "汇率：手动删除"),
    "rate_settings": ("Курсы: настройки", "汇率：设置"),
    "ocr": ("Сканы (OCR)", "扫描件识别"),
    "scan_to_source": ("Сканы → источник", "扫描件→数据源"),
    "settings": ("Настройки", "设置"),
    "export": ("Выгрузка журнала", "导出日志"),
    "login": ("Вход", "登录"),
    "logout": ("Выход", "退出"),
    "user_add": ("Пользователь добавлен", "添加用户"),
    "user_del": ("Пользователь удалён", "删除用户"),
    "user_pw": ("Смена пароля", "修改密码"),
}

_LOCK = threading.Lock()


def log(kind: str, user: str, detail: str, meta: dict | None = None) -> dict:
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
           "user": user or "-", "detail": str(detail)[:1000]}
    if meta:
        rec["meta"] = meta
    os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with _LOCK:
        with open(JOURNAL_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
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


def query(day: str | None = None, q: str | None = None, limit: int = 500,
          kind: str | None = None, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """Фильтр по дню (YYYY-MM-DD) или периоду, виду события и подстроке; свежие первыми."""
    needle = (q or "").strip().lower()
    kinds = {k for k in (kind or "").split(",") if k}
    out = []
    for rec in _iter_records():
        ts = rec.get("ts", "")
        if day and not ts.startswith(day):
            continue
        if date_from and ts[:10] < date_from:
            continue
        if date_to and ts[:10] > date_to:
            continue
        if kinds and rec.get("kind") not in kinds:
            continue
        if needle:
            hay = " ".join([rec.get("kind", ""), kind_label(rec.get("kind", "")), rec.get("user", ""),
                            rec.get("detail", ""),
                            json.dumps(rec.get("meta", {}), ensure_ascii=False, default=str)]).lower()
            if needle not in hay:
                continue
        out.append(rec)
    out.reverse()
    return out[:limit] if limit else out


def kind_label(kind: str, lang: str = "ru") -> str:
    ru, zh = KINDS.get(kind, (kind, kind))
    return zh if lang == "zh" else ru


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


def export_csv(**filters) -> bytes:
    """CSV (UTF-8 с BOM — открывается в Excel) по тем же фильтрам, что query()."""
    buf = io.StringIO()
    wr = csv.writer(buf, delimiter=";")
    wr.writerow(["Время", "Событие", "Код события", "Пользователь", "Что произошло", "Подробности"])
    for rec in query(limit=0, **filters):
        wr.writerow([rec.get("ts"), kind_label(rec.get("kind", "")), rec.get("kind"), rec.get("user"),
                     rec.get("detail"), json.dumps(rec.get("meta", {}), ensure_ascii=False, default=str)
                     if rec.get("meta") else ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")
