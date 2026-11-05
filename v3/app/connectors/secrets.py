# -*- coding: utf-8 -*-
"""Секреты коннекторов и каналов уведомлений.

Пароли, токены и ключи в config/system.yaml открытым текстом не храним.
Способы задать секрет (по убыванию предпочтения):

  password_env: SIBDOC_IMAP_PASSWORD      # имя переменной окружения (рекомендуется)
  password: "${SIBDOC_IMAP_PASSWORD}"     # подстановка переменной в строку
  password: "литерал"                     # работает, но не рекомендуется:
                                          # public_dict() значение не отдаёт,
                                          # в интерфейсе — предупреждение

`${VAR}` поддерживается в любом строковом значении конфигурации
(например, `url: "https://${SAP_HOST}/sap/opu/odata/..."`).
Незаданная переменная подставляется пустой строкой.

Этот модуль общий для app/connectors и app/notify.
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# имена ключей, значения которых считаем секретными, даже если параметр
# не объявлен секретом (заголовки, словари params и т.п.)
SENSITIVE_KEY = re.compile(
    r"(pass(word|wd|phrase)?|secret|token|api[_-]?key|apikey|private|credential|"
    r"authorization|cookie|signature|^key$|_key$|-key$)", re.I)


def expand_env(value):
    """Подставить ${VAR} из окружения (рекурсивно для списков и словарей)."""
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    return value


def has_env_ref(value) -> bool:
    return isinstance(value, str) and bool(_ENV_REF.search(value))


def env_refs(value) -> list[str]:
    return _ENV_REF.findall(value) if isinstance(value, str) else []


def resolve_secret(cfg: dict, key: str) -> str:
    """Значение секрета: cfg[key + "_env"] -> переменная окружения; иначе cfg[key] (с ${VAR})."""
    env_name = cfg.get(f"{key}_env")
    if env_name:
        return os.environ.get(str(env_name).strip(), "")
    val = cfg.get(key)
    if val is None:
        return ""
    return str(expand_env(str(val)))


def secret_source(cfg: dict, key: str) -> str:
    """Откуда берётся секрет: env | env_ref | literal | none."""
    if cfg.get(f"{key}_env"):
        return "env"
    val = cfg.get(key)
    if val in (None, ""):
        return "none"
    if has_env_ref(str(val)):
        return "env_ref"
    return "literal"


def mask_value(value):
    """Значение для показа в интерфейсе: секретные ключи словарей -> '***',
    пароли внутри URL (user:pass@) вырезаются."""
    if isinstance(value, dict):
        return {k: ("***" if SENSITIVE_KEY.search(str(k)) and v not in (None, "")
                    and not has_env_ref(str(v)) else mask_value(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    if isinstance(value, str):
        return redact(value, [])
    return value


def redact(text, secrets) -> str:
    """Убрать из текста значения секретов и типовые места их появления."""
    out = str(text)
    vals = set()
    for s in secrets or []:
        if s and len(str(s)) >= 4:
            s = str(s)
            vals.add(s)
            vals.add(quote(s, safe=""))
            vals.add(quote(s))
    for s in sorted(vals, key=len, reverse=True):
        out = out.replace(s, "***")
    # user:password@host в URL
    out = re.sub(r"(://[^/\s:@]+:)[^@\s/]+@", r"\1***@", out)
    # ключи и токены в query-строке
    out = re.sub(r"(?i)([?&](?:key|access_token|token|api_key|apikey|signature|"
                 r"appsecret|app_secret|password|client_secret)=)[^&\s#'\"]+", r"\1***", out)
    # токен Telegram-бота в пути /bot<id>:<secret>/
    out = re.sub(r"/bot\d+:[A-Za-z0-9_-]+", "/bot***", out)
    return out


def to_bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on", "да")
