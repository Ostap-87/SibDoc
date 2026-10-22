# -*- coding: utf-8 -*-
"""Коннектор «Почта (IMAP)»: забирает вложения из писем брокеров и поставщиков.

Возможности:
  * IMAP по SSL (порт 993) или STARTTLS (порт 143); без шифрования не подключается;
  * папка (в т. ч. русские/китайские имена — кодируются в modified UTF-7);
  * фильтры: от кого (адреса или домены, подстрока), тема содержит (любое из),
    только непрочитанные, за последние N дней;
  * вложения по маскам (xlsx, xls, csv, xml, pdf, zip); zip распаковывается,
    из него берутся подходящие файлы (защита от zip-slip и zip-бомб — лимиты);
  * имена вложений декодируются: RFC 2047 (=?GB2312?B?...?=, =?UTF-8?...),
    RFC 2231 (filename*=gbk''...), «сырые» GBK/UTF-8 байты; GB2312 читается
    как GB18030 (китайские клиенты пишут GB2312, а символы берут из GBK);
  * слот — по slot_rules (имя файла и отправитель) и slots (префикс имени);
  * после успешной загрузки: пометить письмо прочитанным или переместить в папку;
  * повторно один и тот же файл не забирается: Message-ID + имя вложения
    хранятся в storage/connectors/<id>.seen.json.

  connectors:
    mail_broker:
      type: imap
      enabled: false
      label: Почта брокеров
      host: imap.company.ru
      port: 993
      security: ssl                    # ssl | starttls
      username: sibdoc@company.ru
      password_env: SIBDOC_IMAP_PASSWORD
      folder: INBOX
      from: ["@cn-broker.example", "declarant@broker-ru.example"]
      subject_contains: ["报关单", "ДТ", "declaration"]
      unseen_only: false
      since_days: 14
      attachments: ["*.xlsx", "*.xls", "*.csv", "*.xml", "*.pdf", "*.zip"]
      after_success: none              # none | mark_seen | move
      move_to: "SibDoc/Обработано"
      slot_rules:
        - {pattern: '(?i)(报关单|customs.?dec|cn_dec)', slot: cn_dec}
        - {pattern: '(?i)((?<![a-zа-я])ДТ(?![a-zа-я])|ESADout|gtd|ru_dt)', slot: ru_dt}
        - {pattern: '(?i)\\.xml$', sender: '(?i)@broker-ru\\.example$', slot: ru_dt}
"""
from __future__ import annotations

import base64
import email
import email.header
import email.utils
import hashlib
import imaplib
import re
import ssl
from datetime import date, timedelta

from . import fileutil
from .base import (Connector, ConnectorError, P, SLOT_PARAMS, STAGE_READY, TLS_PARAMS,
                   SeenStore)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_HDR_FIELDS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)])"


# ====================================================================== helpers
def imap_utf7(name: str) -> str:
    """Имя папки -> modified UTF-7 (RFC 3501): 'Входящие' -> '&BBIERQQ+BDQETwRJBDgENQ-'."""
    out, buf = [], []

    def flush():
        if buf:
            raw = "".join(buf).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode().rstrip("=").replace("/", ",") + "-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def quote_mailbox(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def imap_date(d: date) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def decode_header_text(value) -> str:
    """Заголовок письма -> текст (RFC 2047, GB2312/GBK/UTF-8, «сырые» 8-битные байты)."""
    if value is None:
        return ""
    try:
        parts = email.header.decode_header(value)
    except Exception:
        return fileutil.fix_surrogates(str(value))
    out = []
    for chunk, cs in parts:
        if isinstance(chunk, str):
            out.append(fileutil.fix_surrogates(chunk))
        else:
            out.append(fileutil.decode_with_charset(chunk, cs))
    return "".join(out).strip()


def attachment_filename(part) -> str | None:
    """Имя вложения с декодированием RFC 2231 / RFC 2047 / сырых GBK-байтов."""
    for header, param in (("content-disposition", "filename"), ("content-type", "name")):
        raw = part.get_param(param, None, header)
        if raw in (None, ""):
            continue
        if isinstance(raw, tuple):          # RFC 2231: (charset, language, value)
            cs, _lang, val = raw
            name = fileutil.decode_with_charset(val.encode("latin-1", "surrogateescape"), cs)
        else:
            name = decode_header_text(email.utils.unquote(str(raw)))
        name = fileutil.safe_filename(name, default="")
        if name:
            return name
    return None


def _parse_fetch(data) -> dict[int, bytes]:
    """Ответ UID FETCH -> {uid: байты литерала}. UID может стоять до или после литерала."""
    out: dict[int, bytes] = {}
    items = list(data or [])
    for i, item in enumerate(items):
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        head = item[0].decode("ascii", "replace") if isinstance(item[0], bytes) else str(item[0])
        m = re.search(r"UID (\d+)", head)
        if not m and i + 1 < len(items) and isinstance(items[i + 1], bytes):
            m = re.search(r"UID (\d+)", items[i + 1].decode("ascii", "replace"))
        if m:
            out[int(m.group(1))] = item[1]
    return out


# ====================================================================== connector
class ImapMailConnector(Connector):
    type = "imap"
    title = "Почта (IMAP): вложения писем"
    stage = STAGE_READY
    params = [
        P("host", "Сервер IMAP", required=True, example="imap.company.ru"),
        P("port", "Порт", example="993 (ssl) / 143 (starttls)"),
        P("security", "Шифрование", example="ssl | starttls"),
        P("username", "Логин (почтовый ящик)", required=True, example="sibdoc@company.ru"),
        P("password", "Пароль (пароль приложения)", required=True, secret=True,
          example="password_env: SIBDOC_IMAP_PASSWORD"),
        P("folder", "Папка", example="INBOX"),
        P("from", "От кого (адреса или домены)",
          example='["@cn-broker.example", "declarant@broker-ru.example"]'),
        P("subject_contains", "Тема содержит (любое из)", example='["报关单", "ДТ"]'),
        P("unseen_only", "Только непрочитанные", example="false"),
        P("since_days", "За последние N дней", example="14"),
        P("attachments", "Маски вложений",
          example='["*.xlsx", "*.xls", "*.csv", "*.xml", "*.pdf", "*.zip"]'),
        P("after_success", "После загрузки", example="none | mark_seen | move"),
        P("move_to", "Папка для обработанных писем", example="SibDoc/Обработано"),
        P("max_messages", "Писем за один опрос (новейшие)", example="200"),
        P("zip_max_total_mb", "Лимит распаковки zip, МБ", example="200"),
    ] + SLOT_PARAMS + TLS_PARAMS

    def _extra_missing(self) -> list[str]:
        if self._after() == "move" and not self.opt("move_to"):
            return ["move_to"]
        return []

    def _after(self) -> str:
        return str(self.opt("after_success", "none")).strip().lower()

    def _masks(self) -> list[str]:
        return self.opt_list("attachments") or list(fileutil.DEFAULT_MASKS)

    # ---------------------------------------------------------------- connect
    def _ssl_context(self) -> ssl.SSLContext:
        ca = self.opt("ca_bundle")
        ctx = ssl.create_default_context(cafile=str(ca) if ca else None)
        if not self.opt_bool("verify_tls", True):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _connect(self):
        host = str(self.opt("host", ""))
        security = str(self.opt("security", "ssl")).strip().lower()
        if security not in ("ssl", "starttls"):
            raise ConnectorError("security: ssl или starttls (без шифрования не подключаемся)")
        port = self.opt_int("port", 993 if security == "ssl" else 143)
        timeout = self.opt_int("timeout_sec", 30)
        try:
            if security == "ssl":
                conn = imaplib.IMAP4_SSL(host, port, ssl_context=self._ssl_context(),
                                         timeout=timeout)
            else:
                conn = imaplib.IMAP4(host, port, timeout=timeout)
                conn.starttls(ssl_context=self._ssl_context())
        except Exception as exc:
            raise ConnectorError(f"нет соединения с {host}:{port} ({security}): {exc}") from exc
        user = str(self.opt("username", ""))
        password = self.secret("password")
        try:
            try:
                conn.login(user, password)
            except UnicodeEncodeError:
                # imaplib шлёт LOGIN в ASCII; пароль с кириллицей — через AUTHENTICATE PLAIN
                conn.authenticate("PLAIN", lambda _: f"\0{user}\0{password}".encode("utf-8"))
        except Exception as exc:
            self._logout(conn)
            raise ConnectorError(f"вход не выполнен для {user}: {exc}") from exc
        return conn

    @staticmethod
    def _logout(conn) -> None:
        try:
            conn.logout()
        except Exception:
            pass

    def _select(self, conn, readonly: bool) -> str:
        folder = str(self.opt("folder", "INBOX"))
        typ, data = conn.select(quote_mailbox(imap_utf7(folder)), readonly=readonly)
        if typ != "OK":
            raise ConnectorError(f"папка не найдена или недоступна: {folder}")
        return folder

    # ---------------------------------------------------------------- search
    def _search(self, conn) -> list[int]:
        crit: list[str] = []
        if self.opt_bool("unseen_only", False):
            crit.append("UNSEEN")
        days = self.opt_int("since_days", 30)
        if days > 0:
            crit += ["SINCE", imap_date(date.today() - timedelta(days=days))]
        typ, data = conn.uid("SEARCH", None, *(crit or ["ALL"]))
        if typ != "OK":
            raise ConnectorError(f"поиск писем не выполнен: {data!r}"[:200])
        uids = b" ".join(d for d in (data or []) if d).split()
        out = sorted(int(u) for u in uids if u.isdigit())
        return out[-self.opt_int("max_messages", 200):]

    def _headers(self, conn, uids: list[int]) -> dict[int, email.message.Message]:
        out: dict[int, email.message.Message] = {}
        for i in range(0, len(uids), 100):
            chunk = uids[i:i + 100]
            typ, data = conn.uid("FETCH", ",".join(str(u) for u in chunk), _HDR_FIELDS)
            if typ != "OK":
                raise ConnectorError("не удалось прочитать заголовки писем")
            for uid, raw in _parse_fetch(data).items():
                out[uid] = email.message_from_bytes(raw)
        return out

    def _sender(self, hdr) -> str:
        addrs = email.utils.getaddresses([decode_header_text(hdr.get("From"))])
        return (addrs[0][1] if addrs else "").lower()

    def _match(self, hdr) -> bool:
        senders = [str(s).lower() for s in self.opt_list("from")]
        if senders:
            addrs = [a.lower() for _, a in
                     email.utils.getaddresses([decode_header_text(hdr.get("From"))])]
            if not any(s in a for s in senders for a in addrs):
                return False
        subj_needles = [str(s).lower() for s in self.opt_list("subject_contains")]
        if subj_needles:
            subject = decode_header_text(hdr.get("Subject")).lower()
            if not any(n in subject for n in subj_needles):
                return False
        return True

    def _matching(self, conn) -> tuple[list[int], dict]:
        uids = self._search(conn)
        if not uids:
            return [], {}
        headers = self._headers(conn, uids)
        return [u for u in uids if u in headers and self._match(headers[u])], headers

    @staticmethod
    def _message_id(hdr) -> str:
        mid = (hdr.get("Message-ID") or "").strip()
        if mid:
            return mid
        raw = "|".join(str(hdr.get(k) or "") for k in ("From", "Date", "Subject"))
        return "sha1:" + hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()

    def _fetch_raw(self, conn, uid: int) -> bytes:
        typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            raise ConnectorError(f"письмо UID {uid} не прочитано")
        got = _parse_fetch(data)
        if uid in got:
            return got[uid]
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2:
                return item[1]
        raise ConnectorError(f"письмо UID {uid}: пустой ответ сервера")

    def _after_success(self, conn, uid: int) -> None:
        mode = self._after()
        if mode == "mark_seen":
            conn.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")
        elif mode == "move":
            target = quote_mailbox(imap_utf7(str(self.opt("move_to"))))
            caps = getattr(conn, "capabilities", ()) or ()
            caps = {c.decode() if isinstance(c, bytes) else str(c) for c in caps}
            if "MOVE" in {c.upper() for c in caps}:
                typ, _ = conn.uid("MOVE", str(uid), target)
            else:
                typ, _ = conn.uid("COPY", str(uid), target)
                if typ == "OK":
                    conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
                    conn.expunge()
            if typ != "OK":
                self.last_notes.append(f"письмо UID {uid} не перемещено в {self.opt('move_to')}")

    # ---------------------------------------------------------------- api
    def _test(self) -> dict:
        conn = self._connect()
        try:
            folder = self._select(conn, readonly=True)
            matched, _ = self._matching(conn)
        finally:
            self._logout(conn)
        crit = []
        if self.opt_list("from"):
            crit.append("отправители: " + ", ".join(map(str, self.opt_list("from"))))
        if self.opt_list("subject_contains"):
            crit.append("тема: " + ", ".join(map(str, self.opt_list("subject_contains"))))
        if self.opt_bool("unseen_only"):
            crit.append("только непрочитанные")
        crit.append(f"за {self.opt_int('since_days', 30)} дн.")
        return {"ok": True, "detail": f"вход выполнен, папка «{folder}»: подходящих писем "
                                      f"{len(matched)} ({'; '.join(crit)})",
                "messages": len(matched)}

    def _pull(self, dest_dir: str) -> list[dict]:
        masks = self._masks()
        seen = SeenStore(self.id, self.opt("state_dir"))
        conn = self._connect()
        out: list[dict] = []
        used: set = set()
        try:
            self._select(conn, readonly=self._after() == "none")
            matched, headers = self._matching(conn)
            for uid in matched:
                hdr = headers[uid]
                mid = self._message_id(hdr)
                sender = self._sender(hdr)
                subject = decode_header_text(hdr.get("Subject"))
                try:
                    msg = email.message_from_bytes(self._fetch_raw(conn, uid))
                except ConnectorError as exc:
                    self.last_notes.append(str(exc))
                    continue
                found = 0
                failed = False
                for part in msg.walk():
                    if part.is_multipart():
                        continue
                    fname = attachment_filename(part)
                    if not fname or not fileutil.match_masks(fname, masks):
                        continue
                    found += 1
                    key = SeenStore.key(mid, fname)
                    if key in seen:
                        continue
                    try:
                        data = part.get_payload(decode=True) or b""
                        out.extend(self._store_file(dest_dir, fname, data, sender=sender,
                                                    masks=masks, origin=f"письмо «{subject}» "
                                                                        f"от {sender}",
                                                    used=used))
                        seen.add(key)
                    except OSError as exc:
                        failed = True
                        self.last_notes.append(f"{fname}: не сохранён ({exc})")
                if found and not failed and self._after() != "none":
                    try:
                        self._after_success(conn, uid)
                    except Exception as exc:
                        self.last_notes.append(f"письмо UID {uid}: {exc}")
        finally:
            seen.save()
            self._logout(conn)
        return out
