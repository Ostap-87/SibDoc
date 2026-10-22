# -*- coding: utf-8 -*-
"""Уведомления: WeCom (企业微信), Telegram, электронная почта.

Конфигурация — config/system.yaml -> notifications (каналы, по умолчанию выключены):

  notifications:
    wecom_team:
      type: wecom                      # wecom | telegram | email
      enabled: false
      key_env: SIBDOC_WECOM_KEY
      events: [run_done, issues_found, connector_error]   # пусто или "*" — все события

Публичный API:
  send(cfg, event, text, files=None, user="system") -> [{"channel", "type", "ok", "detail"}]
      шлёт во все включённые каналы, подписанные на событие; ошибки не роняют вызов;
  test_channel(cfg, channel_id, user="system") -> {"channel", "type", "ok", "detail"}
      тестовое сообщение (в т. ч. в выключенный канал — проверить до включения);
  list_channels(cfg), catalog().
Каждая отправка пишется в журнал (kind "notify") без секретов.
"""
from __future__ import annotations

from .base import EVENTS, Channel, NotifyError
from .smtp_email import EmailChannel
from .telegram import TelegramChannel
from .wecom import WeComChannel

CHANNEL_TYPES = {
    "wecom": WeComChannel,
    "telegram": TelegramChannel,
    "email": EmailChannel,
}

__all__ = ["send", "test_channel", "list_channels", "get_channel", "catalog",
           "CHANNEL_TYPES", "EVENTS", "Channel", "NotifyError"]


def _journal(user: str, detail: str, meta: dict) -> None:
    try:
        from app.journal import log
        log("notify", user or "system", detail, meta)
    except Exception:  # pragma: no cover
        pass


def _channel_cfgs(cfg: dict) -> list[tuple[str, dict]]:
    raw = (cfg or {}).get("notifications") or {}
    if isinstance(raw, dict) and isinstance(raw.get("channels"), (dict, list)):
        raw = raw["channels"]
    out: list[tuple[str, dict]] = []
    if isinstance(raw, dict):
        out = [(str(k), v) for k, v in raw.items() if isinstance(v, dict)]
    elif isinstance(raw, list):
        out = [(str(v.get("id") or f"channel_{i + 1}"), v) for i, v in enumerate(raw)
               if isinstance(v, dict)]
    return out


def list_channels(cfg: dict) -> list[Channel]:
    out = []
    for cid, ccfg in _channel_cfgs(cfg):
        cls = CHANNEL_TYPES.get(str(ccfg.get("type", "")))
        if cls is not None:
            out.append(cls(cid, ccfg))
    return out


def get_channel(cfg: dict, cid: str) -> Channel | None:
    for ch in list_channels(cfg):
        if ch.id == cid:
            return ch
    return None


def catalog() -> list[dict]:
    return [{"type": t, "title": c.title, "params": [dict(p) for p in c.params]}
            for t, c in CHANNEL_TYPES.items()]


def _deliver(ch: Channel, event: str, text: str, files, user: str) -> dict:
    res = {"channel": ch.id, "type": ch.type, "ok": False, "detail": ""}
    try:
        miss = ch.missing()
        if miss:
            raise NotifyError(f"не заполнено: {', '.join(miss)}")
        got = ch.send(event, text, files) or {}
        res["ok"] = True
        res["detail"] = ch.redact(got.get("detail", "отправлено"))
        for k in ("parts", "files"):
            if k in got:
                res[k] = got[k]
    except Exception as exc:
        msg = str(exc) if isinstance(exc, NotifyError) else f"{type(exc).__name__}: {exc}"
        res["detail"] = ch.redact(msg)[:500]
    _journal(user, f"{ch.id} ({ch.title}): {EVENTS.get(event, event)} — "
                   f"{'отправлено' if res['ok'] else 'ошибка'}: {res['detail']}",
             {"channel": ch.id, "type": ch.type, "event": event, "ok": res["ok"]})
    return res


def send(cfg: dict, event: str, text: str, files: list[str] | None = None,
         user: str = "system") -> list[dict]:
    """Разослать событие во все включённые каналы, подписанные на него."""
    out = []
    try:
        channels = list_channels(cfg)
    except Exception as exc:  # pragma: no cover
        return [{"channel": "-", "type": "-", "ok": False, "detail": f"ошибка конфигурации: {exc}"}]
    for ch in channels:
        if not ch.enabled or not ch.wants(event):
            continue
        out.append(_deliver(ch, event, text, files, user))
    return out


def test_channel(cfg: dict, channel_id: str, user: str = "system") -> dict:
    """Тестовое сообщение в канал (даже выключенный)."""
    ch = get_channel(cfg, channel_id)
    if ch is None:
        return {"channel": channel_id, "type": "-", "ok": False, "detail": "канал не найден"}
    text = (f"SibDoc: тестовое сообщение канала «{ch.label}». "
            f"Если вы его видите — уведомления настроены.")
    return _deliver(ch, "test", text, None, user)
