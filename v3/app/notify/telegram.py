# -*- coding: utf-8 -*-
"""Telegram-бот: sendMessage (до 4096 символов) и sendDocument (до 50 МБ, подпись до 1024).

Токен — из переменной окружения (token_env). Если api.telegram.org недоступен
(например, из КНР), укажите api_url своего сервера Bot API или proxy.

  notifications:
    telegram_ops:
      type: telegram
      enabled: false
      token_env: SIBDOC_TG_TOKEN
      chat_id: "-1001234567890"
      events: [issues_found, connector_error]
"""
from __future__ import annotations

import os

from .base import Channel, NotifyError, P, split_text, utf16_len

API = "https://api.telegram.org"
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
FILE_MAX = 50 * 2**20


class TelegramChannel(Channel):
    type = "telegram"
    title = "Telegram-бот"
    params = [
        P("token", "Токен бота", required=True, secret=True, example="token_env: SIBDOC_TG_TOKEN"),
        P("chat_id", "ID чата или группы", required=True, example="-1001234567890"),
        P("events", "События", example="[issues_found, connector_error]"),
        P("parse_mode", "Разметка (пусто — обычный текст)", example="HTML"),
        P("send_files", "Отправлять файлы отчётов", example="true"),
        P("api_url", "Адрес Bot API (свой сервер)", example="https://api.telegram.org"),
        P("proxy", "Прокси для запросов", example="http://proxy.company.ru:3128"),
    ]

    def _call(self, requests, method: str, **kw) -> dict:
        url = f"{str(self.opt('api_url', API)).rstrip('/')}/bot{self.secret('token')}/{method}"
        proxy = self.opt("proxy")
        resp = requests.post(url, timeout=kw.pop("timeout", 30), verify=self._verify(),
                             proxies={"https": proxy, "http": proxy} if proxy else None, **kw)
        try:
            data = resp.json()
        except ValueError as exc:
            raise NotifyError(f"Telegram {method}: HTTP {resp.status_code}, ответ не JSON") from exc
        if not data.get("ok"):
            raise NotifyError(f"Telegram {method}: {data.get('error_code')} — {data.get('description')}")
        return data

    def send(self, event: str, text: str, files: list[str] | None = None) -> dict:
        if not self.secret("token"):
            raise NotifyError("не задан токен бота (token_env)")
        chat_id = str(self.opt("chat_id", ""))
        if not chat_id:
            raise NotifyError("не задан chat_id")
        requests = self._requests()
        parts = split_text(text, TEXT_LIMIT, unit=utf16_len, max_parts=self.opt_int("max_parts", 3))
        for part in parts:
            payload = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if self.opt("parse_mode"):
                payload["parse_mode"] = self.opt("parse_mode")
            self._call(requests, "sendMessage", json=payload)
        sent, notes = [], []
        if files and self.opt_bool("send_files", True):
            ok_files, notes = self._files(files, FILE_MAX)
            for path in ok_files:
                name = os.path.basename(path)
                with open(path, "rb") as fh:
                    self._call(requests, "sendDocument", timeout=120,
                               data={"chat_id": chat_id, "caption": name[:CAPTION_LIMIT]},
                               files={"document": (name, fh)})
                sent.append(name)
        detail = f"отправлено сообщений: {len(parts)}" + (f", файлов: {len(sent)}" if sent else "")
        if notes:
            detail += "; " + "; ".join(notes)
        return {"detail": detail, "parts": len(parts), "files": sent}
