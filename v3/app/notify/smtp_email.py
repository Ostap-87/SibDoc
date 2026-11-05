# -*- coding: utf-8 -*-
"""Электронная почта (SMTP): SSL (порт 465) или STARTTLS (порт 587), вложения.

Пароль — из переменной окружения (password_env).

  notifications:
    email_team:
      type: email
      enabled: false
      host: smtp.company.ru
      port: 465
      security: ssl                 # ssl | starttls
      username: sibdoc@company.ru
      password_env: SIBDOC_SMTP_PASSWORD
      from: "SibDoc <sibdoc@company.ru>"
      to: ["customs-team@company.ru"]
      subject_prefix: "[SibDoc]"
      events: [run_done, issues_found]
"""
from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage

from .base import EVENTS, Channel, NotifyError, P

FILE_MAX = 20 * 2**20


class EmailChannel(Channel):
    type = "email"
    title = "Электронная почта (SMTP)"
    params = [
        P("host", "Сервер SMTP", required=True, example="smtp.company.ru"),
        P("port", "Порт", example="465 (ssl) / 587 (starttls)"),
        P("security", "Шифрование", example="ssl | starttls"),
        P("username", "Логин", example="sibdoc@company.ru"),
        P("password", "Пароль", secret=True, example="password_env: SIBDOC_SMTP_PASSWORD"),
        P("from", "Отправитель", required=True, example="SibDoc <sibdoc@company.ru>"),
        P("to", "Получатели", required=True, example='["customs-team@company.ru"]'),
        P("subject_prefix", "Префикс темы", example="[SibDoc]"),
        P("events", "События", example="[run_done, issues_found]"),
        P("send_files", "Прикладывать файлы отчётов", example="true"),
        P("max_attach_mb", "Лимит вложений, МБ", example="20"),
    ]

    def _ctx(self) -> ssl.SSLContext:
        ca = self.opt("ca_bundle")
        ctx = ssl.create_default_context(cafile=str(ca) if ca else None)
        if not self.opt_bool("verify_tls", True):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _message(self, event: str, text: str, files: list[str]) -> EmailMessage:
        msg = EmailMessage()
        first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        subject = f"{self.opt('subject_prefix', '[SibDoc]')} {EVENTS.get(event, event)}"
        if first:
            subject += f": {first}"
        msg["Subject"] = subject[:200]
        msg["From"] = str(self.opt("from"))
        msg["To"] = ", ".join(str(x) for x in self.opt_list("to"))
        msg.set_content(text or "")
        for path in files:
            ctype, _ = mimetypes.guess_type(path)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            with open(path, "rb") as fh:
                msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype,
                                   filename=os.path.basename(path))
        return msg

    def send(self, event: str, text: str, files: list[str] | None = None) -> dict:
        host = str(self.opt("host", ""))
        if not host or not self.opt("from") or not self.opt_list("to"):
            raise NotifyError("не заданы host / from / to")
        security = str(self.opt("security", "ssl")).lower()
        if security not in ("ssl", "starttls"):
            raise NotifyError("security: ssl или starttls")
        port = self.opt_int("port", 465 if security == "ssl" else 587)
        ok_files, notes = ([], [])
        if files and self.opt_bool("send_files", True):
            ok_files, notes = self._files(files, self.opt_int("max_attach_mb", 20) * 2**20)
        msg = self._message(event, text, ok_files)
        timeout = self.opt_int("timeout_sec", 30)
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, context=self._ctx(), timeout=timeout)
        else:
            smtp = smtplib.SMTP(host, port, timeout=timeout)
        try:
            if security == "starttls":
                smtp.starttls(context=self._ctx())
            if self.opt("username"):
                smtp.login(str(self.opt("username")), self.secret("password"))
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        detail = f"письмо отправлено: {len(self.opt_list('to'))} получ."
        if ok_files:
            detail += f", вложений: {len(ok_files)}"
        if notes:
            detail += "; " + "; ".join(notes)
        return {"detail": detail, "files": [os.path.basename(f) for f in ok_files]}
