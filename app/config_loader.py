from __future__ import annotations
# -*- coding: utf-8 -*-
"""Загрузка и валидация конфигурации системы.

Вся вариативность продукта — в config/system.yaml:
источники, маппинг колонок, параметры проверок, секции для DQ-детекции.
"""
import os
import yaml

DEFAULT_CONFIG_PATH = os.environ.get(
    "PCC_CONFIG", os.path.join(os.path.dirname(__file__), "..", "config", "system.yaml"))


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


def save_rules(rule_file: str, rules: dict, base_dir: str | None = None):
    """Обновить файл правил (используется API для редактирования без рестарта)."""
    base = base_dir or os.path.join(os.path.dirname(__file__), "..", "config", "rules")
    path = os.path.join(base, rule_file)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(rules, f, allow_unicode=True, sort_keys=False)
    return path


def load_rules(rule_file: str, base_dir: str | None = None) -> dict:
    base = base_dir or os.path.join(os.path.dirname(__file__), "..", "config", "rules")
    path = os.path.join(base, rule_file)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
