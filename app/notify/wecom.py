# -*- coding: utf-8 -*-
"""WeCom (企业微信): групповой бот через вебхук.

  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<KEY>
    {"msgtype": "markdown", "markdown": {"content": "..."}}   — до 4096 байт UTF-8
  https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key=<KEY>&type=file
    multipart, поле media; файл 5 байт … 20 МБ -> {"errcode": 0, "media_id": "..."}
    затем {"msgtype": "file", "file": {"media_id": "..."}}
Ответ API: errcode = 0 — успех. Лимит бота — 20 сообщений в минуту.

Ключ — из переменной окружения (key_env), в конфиг не пишется:
  notifications:
    wecom_team:
      type: wecom
      enabled: false
      key_env: SIBDOC_WECOM_KEY
      events: [run_done, issues_found, connector_error]
      send_files: true
"""
from __future__ import annotations

import os

from .base import Channel, NotifyError, P, split_text, utf8_len

API = "https://qyapi.weixin.qq.com/cgi-bin/webhook"
MARKDOWN_LIMIT = 4096
FILE_MAX = 20 * 2**20


class WeComChannel(Channel):
    type = "wecom"
    title = "WeCom (企业微信): групповой бот"
    params = [
        P("key", "Ключ вебхука бота (key=…)", required=True, secret=True,
          example="key_env: SIBDOC_WECOM_KEY"),
        P("events", "События", example="[run_done, issues_found, connector_error]"),
        P("send_files", "Отправлять файлы отчётов", example="true"),
        P("max_parts", "Сообщений на одно событие (длинный текст)", example="3"),
    ]

    def _api(self) -> str:
        return str(self.opt("api_url", API)).rstrip("/")

    def _check(self, resp, what: str) -> dict:
        try:
            data = resp.json()
        except ValueError as exc:
            raise NotifyError(f"WeCom {what}: HTTP {resp.status_code}, ответ не JSON") from exc
        if data.get("errcode") != 0:
            raise NotifyError(f"WeCom {what}: errcode {data.get('errcode')} — {data.get('errmsg')}")
        return data

    def send(self, event: str, text: str, files: list[str] | None = None) -> dict:
        key = self.secret("key")
        if not key:
            raise NotifyError("не задан ключ вебхука (key_env)")
        requests = self._requests()
        parts = split_text(text, MARKDOWN_LIMIT, unit=utf8_len, max_parts=self.opt_int("max_parts", 3))
        for part in parts:
            resp = requests.post(f"{self._api()}/send", params={"key": key},
                                 json={"msgtype": "markdown", "markdown": {"content": part}},
                                 timeout=15, verify=self._verify())
            self._check(resp, "сообщение")
        sent, notes = [], []
        if files and self.opt_bool("send_files", True):
            ok_files, notes = self._files(files, FILE_MAX)
            for path in ok_files:
                if os.path.getsize(path) < 5:
                    notes.append(f"{os.path.basename(path)}: меньше 5 байт, не отправлен")
                    continue
                with open(path, "rb") as fh:
                    resp = requests.post(f"{self._api()}/upload_media",
                                         params={"key": key, "type": "file"},
                                         files={"media": (os.path.basename(path), fh,
                                                          "application/octet-stream")},
                                         timeout=60, verify=self._verify())
                media_id = self._check(resp, "загрузка файла").get("media_id")
                resp = requests.post(f"{self._api()}/send", params={"key": key},
                                     json={"msgtype": "file", "file": {"media_id": media_id}},
                                     timeout=15, verify=self._verify())
                self._check(resp, "файл")
                sent.append(os.path.basename(path))
        detail = f"отправлено сообщений: {len(parts)}" + (f", файлов: {len(sent)}" if sent else "")
        if notes:
            detail += "; " + "; ".join(notes)
        return {"detail": detail, "parts": len(parts), "files": sent}
