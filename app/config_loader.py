from __future__ import annotations
# -*- coding: utf-8 -*-
"""Загрузка и валидация конфигурации системы.

Вся вариативность продукта — в config/system.yaml:
источники, маппинг колонок, параметры проверок, секции для DQ-детекции.
Правила проверок (config/rules/*.yaml) меняются через API/UI: перед каждой
записью прежняя версия сохраняется в storage/rules_history/, а разница
«было -> стало» возвращается вызывающему коду для журнала.
"""
import os
import re
import shutil
import time

import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_CONFIG_PATH = os.environ.get("PCC_CONFIG") or os.path.join(PROJECT_ROOT, "config", "system.yaml")
RULES_DIR = os.path.join(PROJECT_ROOT, "config", "rules")
HISTORY_DIR = os.path.join(PROJECT_ROOT, "storage", "rules_history")
_RULE_NAME = re.compile(r"^[A-Za-z0-9_\-]+\.ya?ml$")


def load_config(path: str | None = None) -> dict:
    path = path or DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _validate(cfg, path)
    return cfg


def _validate(cfg: dict, path: str):
    required_top = ["sources", "checks", "data_quality", "output"]
    missing = [k for k in required_top if k not in cfg]
    if missing:
        raise ValueError(f"{path}: missing top-level keys: {missing}")
    for sid, src in cfg["sources"].items():
        if "label" not in src or "required_columns" not in src:
            raise ValueError(f"{path}: source '{sid}' needs label + required_columns")


def known_rule_files(cfg: dict | None = None) -> list[str]:
    """Файлы правил, которые реально используют проверки (только их можно править через API)."""
    cfg = cfg or load_config()
    return [c.get("rules", f"{cid}.yaml") for cid, c in (cfg.get("checks") or {}).items()]


def _rule_path(rule_file: str, base_dir: str | None = None) -> str:
    if not _RULE_NAME.match(rule_file or ""):
        raise ValueError(f"Недопустимое имя файла правил: {rule_file!r}")
    return os.path.join(base_dir or RULES_DIR, rule_file)


def load_rules(rule_file: str, base_dir: str | None = None) -> dict:
    path = _rule_path(rule_file, base_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _flatten(d, prefix="") -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    else:
        out[prefix.rstrip(".")] = d
    return out


def diff_rules(old: dict, new: dict) -> list[dict]:
    """[{key, old, new}] — что изменилось (ключи вложенных словарей через точку)."""
    a, b = _flatten(old or {}), _flatten(new or {})
    return [{"key": k, "old": a.get(k), "new": b.get(k)}
            for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def save_rules(rule_file: str, rules: dict, base_dir: str | None = None) -> dict:
    """Обновить файл правил. -> {"path", "diff", "backup"}.

    Прежняя версия копируется в storage/rules_history/<файл>/<время>.yaml.
    """
    if not isinstance(rules, dict):
        raise ValueError("Правила должны быть словарём (ключ: значение)")
    path = _rule_path(rule_file, base_dir)
    old = load_rules(rule_file, base_dir) if os.path.exists(path) else {}
    backup = ""
    if os.path.exists(path) and base_dir is None:
        hist = os.path.join(HISTORY_DIR, rule_file)
        os.makedirs(hist, exist_ok=True)
        backup = os.path.join(hist, time.strftime("%Y%m%d-%H%M%S") + ".yaml")
        shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(rules, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
    return {"path": path, "diff": diff_rules(old, rules), "backup": backup}


def rules_history(rule_file: str) -> list[dict]:
    hist = os.path.join(HISTORY_DIR, rule_file)
    if not _RULE_NAME.match(rule_file or "") or not os.path.isdir(hist):
        return []
    out = []
    for name in sorted(os.listdir(hist), reverse=True):
        if name.endswith(".yaml"):
            ts = name[:-5]
            out.append({"version": ts, "ts": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"})
    return out


def load_rules_version(rule_file: str, version: str) -> dict:
    if not re.fullmatch(r"\d{8}-\d{6}", version or ""):
        raise ValueError("Недопустимая версия")
    path = os.path.join(HISTORY_DIR, rule_file, version + ".yaml")
    if not _RULE_NAME.match(rule_file or "") or not os.path.exists(path):
        raise ValueError("Версия не найдена")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
