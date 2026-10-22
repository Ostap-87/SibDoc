from __future__ import annotations
# -*- coding: utf-8 -*-
"""Реестр проверок (плагинная архитектура).

Чтобы добавить новую проверку:
1. Создайте модуль в app/checks/my_check.py с функцией run(sources, rules, cfg)
2. Зарегистрируйте:  register("my_check", "N. Моя проверка", run)
3. Включите в config/system.yaml -> checks: my_check: {enabled: true, rules: "my_check.yaml"}

Функция run получает:
  sources — dict[str, Source] загруженных источников (None если не загружен)
  rules   — содержимое config/rules/<файл из system.yaml>
  cfg     — вся системная конфигурация
и возвращает CheckOutput с колонками status/detail.
"""
from app.models import CheckOutput

_REGISTRY: dict[str, tuple[str, callable]] = {}


def register(check_id: str, title: str, fn):
    _REGISTRY[check_id] = (title, fn)


def available() -> list[str]:
    return list(_REGISTRY.keys())


def run_check(check_id: str, sources: dict, rules: dict, cfg: dict) -> CheckOutput:
    title, fn = _REGISTRY[check_id]
    return fn(sources, rules, cfg)


# встроенные проверки
from app.checks import (  # noqa: E402,F401
    completeness, price, hs_code, incoterms, decl_elements, contracts, cross_border,
)
