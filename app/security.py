# -*- coding: utf-8 -*-
"""Учётные записи и сессии (без внешних зависимостей).

Хранение: storage/users.json, пароли — PBKDF2-HMAC-SHA256 (200k итераций), соль случайная.
Сессии: токены в памяти процесса (перезапуск сервера = повторный вход, это нормально).

Роли:
  admin    — всё, включая управление пользователями и правилами
  operator — загрузка данных, запуск проверок, отчёты
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
USERS_FILE = os.path.join(PROJECT_ROOT, "storage", "users.json")

ROLES = ("admin", "operator")
DEFAULT_ADMIN_PASSWORD = "admin"      # смените через интерфейс: Пользователи -> смена пароля
MIN_PASSWORD_LEN = 3
SESSION_TTL_SEC = 12 * 3600


# ---------------------------------------------------------------------------
# Корпоративный вход (SSO/OIDC) — ЗАГОТОВКА, по умолчанию ВЫКЛЮЧЕН.
#
# Как включить позже (без переписывания модуля):
#   1) в config/system.yaml добавить блок:
#        auth:
#          mode: corporate_sso          # local | corporate_sso
#          provider: entra_id           # entra_id | oidc_generic
#          issuer_url: "https://login.microsoftonline.com/<tenant-id>/v2.0"
#          client_id: "..."
#          client_secret: "..."
#          redirect_uri: "https://sibdoc.вашдомен.ru/api/auth/sso/callback"
#          allowed_domain: "sibur.ru"   # только корпоративные почты этого домена
#   2) в server.py добавить /api/auth/sso/login (редирект на провайдера) и
#      /api/auth/sso/callback (обмен кода на токен, проверка домена, создание
#      локальной сессии через create_session) — точки интеграции уже есть ниже.
#
# При corporate_sso локальные пароли остаются для аварийного входа (break-glass)
# и для сервисной учётки admin.


def sso_settings(cfg: dict) -> dict:
    """Настройки корпоративного входа из system.yaml (auth: {...})."""
    auth = (cfg or {}).get("auth") or {}
    if auth.get("mode") not in ("corporate_sso",):
        return {"enabled": False}
    return {
        "enabled": True,
        "provider": auth.get("provider", "oidc_generic"),
        "issuer_url": auth.get("issuer_url", ""),
        "client_id": auth.get("client_id", ""),
        "redirect_uri": auth.get("redirect_uri", ""),
        "allowed_domain": (auth.get("allowed_domain") or "").lower(),
    }


def corporate_email_allowed(email: str, allowed_domain: str) -> bool:
    """Проверка, что email принадлежит корпоративному домену (для будущего SSO)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return False
    return email.split("@", 1)[1] == (allowed_domain or "").lower()


def _hash(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return dk.hex()


def _new_salt() -> str:
    return secrets.token_hex(16)


def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save_users(users: dict) -> None:
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as fh:
        json.dump(users, fh, ensure_ascii=False, indent=2)


def ensure_admin() -> bool:
    """Если пользователей нет — создать admin с паролем по умолчанию. -> был ли создан."""
    if load_users():
        return False
    create_user("admin", DEFAULT_ADMIN_PASSWORD, "admin")
    return True


def create_user(login: str, password: str, role: str = "operator") -> None:
    login = (login or "").strip()
    if not login or any(c in login for c in "/\\ '\""):
        raise ValueError("недопустимый логин")
    if role not in ROLES:
        raise ValueError(f"роль должна быть одной из: {ROLES}")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"пароль минимум {MIN_PASSWORD_LEN} символов")
    users = load_users()
    if login in users:
        raise ValueError("такой пользователь уже есть")
    salt = _new_salt()
    users[login] = {"salt": salt, "hash": _hash(password, salt), "role": role,
                    "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_users(users)


def delete_user(login: str) -> None:
    users = load_users()
    if login not in users:
        raise ValueError("пользователь не найден")
    del users[login]
    save_users(users)


def set_password(login: str, password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"пароль минимум {MIN_PASSWORD_LEN} символов")
    users = load_users()
    if login not in users:
        raise ValueError("пользователь не найден")
    salt = _new_salt()
    users[login]["salt"] = salt
    users[login]["hash"] = _hash(password, salt)
    save_users(users)


def verify(login: str, password: str) -> dict | None:
    rec = load_users().get(login)
    if not rec:
        return None
    if secrets.compare_digest(rec["hash"], _hash(password or "", rec["salt"])):
        return {"login": login, "role": rec["role"]}
    return None


def list_users() -> list[dict]:
    return [{"login": u, "role": r["role"], "created": r.get("created")}
            for u, r in sorted(load_users().items())]


def count_admins() -> int:
    return sum(1 for r in load_users().values() if r.get("role") == "admin")


# --------------------------------------------------------------------- сессии
_sessions: dict[str, dict] = {}


def create_session(login: str) -> str:
    token = secrets.token_urlsafe(32)
    role = load_users().get(login, {}).get("role", "operator")
    _sessions[token] = {"login": login, "role": role, "exp": time.time() + SESSION_TTL_SEC}
    return token


def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    rec = _sessions.get(token)
    if not rec:
        return None
    if rec["exp"] < time.time():
        _sessions.pop(token, None)
        return None
    return rec


def drop_session(token: str | None) -> None:
    if token:
        _sessions.pop(token, None)
