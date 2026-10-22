# -*- coding: utf-8 -*-
"""Коннектор SFTP (paramiko) — защищённый приём файлов (корпоративный контур,
файлы китайского/российского брокера).

Ключ сервера ПРОВЕРЯЕТСЯ всегда:
  * по known_hosts (путь из known_hosts или ~/.ssh/known_hosts), строка
    добавляется командой:  ssh-keyscan -p 22 sftp.example.ru >> known_hosts
    (сверьте отпечаток с администратором сервера: ssh-keygen -lf known_hosts);
  * или по отпечатку host_key_sha256 ("SHA256:..." — как печатает ssh-keygen -lf).
  Незнакомый сервер отклоняется. Принять новый ключ без проверки можно только
  явно: insecure_accept_new_host: true (тогда в test() — предупреждение).

  connectors:
    sftp_in:
      type: sftp
      enabled: false
      label: SFTP брокера
      host: sftp.example.ru
      port: 22
      username: svc_sibdoc
      password_env: SIBDOC_SFTP_PASSWORD   # или key_file (ключ предпочтительнее)
      key_file: /run/secrets/sibdoc_sftp_ed25519
      key_passphrase_env: SIBDOC_SFTP_KEY_PASS
      known_hosts: /app/config/known_hosts
      # host_key_sha256: "SHA256:AbCd..."
      remote_dir: /uploads/sibdoc
      pattern: ["*.xlsx", "*.csv", "*.xml", "*.zip"]
      slots: [wide_table, purchase]
      slot_rules: [{pattern: '(?i)(报关单|customs.?dec)', slot: cn_dec}]
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import posixpath
import stat

from . import fileutil
from .base import Connector, ConnectorError, P, SLOT_PARAMS, STAGE_READY


def key_fingerprint_sha256(key) -> str:
    """Отпечаток ключа в формате OpenSSH: 'SHA256:<base64 без =>'."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _norm_fp(fp: str) -> str:
    fp = str(fp).strip()
    if fp.upper().startswith("SHA256:"):
        fp = fp[7:]
    return fp.rstrip("=")


class SftpConnector(Connector):
    type = "sftp"
    title = "SFTP (файлы брокера, корпоративный обмен)"
    stage = STAGE_READY
    params = [
        P("host", "Сервер SFTP", required=True, example="sftp.example.ru"),
        P("port", "Порт", example="22"),
        P("username", "Логин", required=True, example="svc_sibdoc"),
        P("password", "Пароль", secret=True, example="password_env: SIBDOC_SFTP_PASSWORD"),
        P("key_file", "Файл закрытого ключа", example="/run/secrets/sibdoc_sftp_ed25519"),
        P("key_passphrase", "Пароль к ключу", secret=True,
          example="key_passphrase_env: SIBDOC_SFTP_KEY_PASS"),
        P("known_hosts", "Файл known_hosts", example="/app/config/known_hosts"),
        P("host_key_sha256", "Отпечаток ключа сервера", example="SHA256:AbCdEf..."),
        P("insecure_accept_new_host", "Принять новый ключ без проверки (небезопасно)",
          example="false"),
        P("remote_dir", "Папка на сервере", example="/uploads/sibdoc"),
        P("pattern", "Маска файлов", example='["*.xlsx", "*.csv", "*.xml", "*.zip"]'),
        P("max_file_mb", "Максимальный размер файла, МБ", example="200"),
    ] + SLOT_PARAMS

    def _extra_missing(self) -> list[str]:
        if not self.secret("password") and not self.opt("key_file"):
            return ["password_env или key_file"]
        return []

    def warnings(self) -> list[str]:
        out = super().warnings()
        if self.opt_bool("insecure_accept_new_host"):
            out.append("insecure_accept_new_host: true — ключ сервера не проверяется "
                       "(риск подмены сервера)")
        return out

    # ---------------------------------------------------------------- connect
    def _connect(self):
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("paramiko не установлен (pip install paramiko)") from exc
        host = self.opt("host", "")
        port = self.opt_int("port", 22)
        warnings: list[str] = []
        client = paramiko.SSHClient()
        fp = self.opt("host_key_sha256")
        insecure = self.opt_bool("insecure_accept_new_host")
        kh_cfg = self.opt("known_hosts")
        kh_path = os.path.expanduser(str(kh_cfg or "~/.ssh/known_hosts"))

        if fp:
            expected = _norm_fp(fp)

            class _FingerprintPolicy(paramiko.MissingHostKeyPolicy):
                def missing_host_key(self, client_, hostname, key):
                    got = key_fingerprint_sha256(key)
                    if _norm_fp(got) != expected:
                        raise paramiko.SSHException(
                            f"отпечаток ключа сервера {hostname} ({got}) не совпадает "
                            f"с host_key_sha256 — соединение отклонено")

            if kh_cfg and os.path.isfile(kh_path):
                client.load_host_keys(kh_path)
            client.set_missing_host_key_policy(_FingerprintPolicy())
        else:
            if os.path.isfile(kh_path):
                client.load_host_keys(kh_path)
            elif not insecure:
                raise ConnectorError(
                    f"ключ сервера не проверить: нет файла known_hosts ({kh_path}) и не задан "
                    f"host_key_sha256. Добавьте ключ: ssh-keyscan -p {port} {host} >> known_hosts "
                    f"(отпечаток сверьте с администратором сервера)")
            if insecure:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                warnings.append("ВНИМАНИЕ: insecure_accept_new_host: true — новый ключ сервера "
                                "принят без проверки; сверьте отпечаток и уберите эту опцию")
            else:
                client.set_missing_host_key_policy(paramiko.RejectPolicy())

        args = dict(hostname=host, port=port, username=self.opt("username", ""),
                    timeout=self.opt_int("timeout_sec", 15),
                    allow_agent=False, look_for_keys=False)
        key_file = self.opt("key_file")
        if key_file:
            args["key_filename"] = os.path.expanduser(str(key_file))
            if self.secret("key_passphrase"):
                args["passphrase"] = self.secret("key_passphrase")
        else:
            args["password"] = self.secret("password")
        try:
            client.connect(**args)
        except paramiko.BadHostKeyException as exc:
            raise ConnectorError(
                f"ключ сервера {host} НЕ совпадает с known_hosts — возможна подмена сервера; "
                f"соединение отклонено ({exc})") from exc
        except paramiko.SSHException as exc:
            text = str(exc)
            if "known_hosts" in text:
                text = (f"сервер {host}:{port} отсутствует в known_hosts ({kh_path}) — "
                        f"соединение отклонено. Добавьте ключ: ssh-keyscan -p {port} {host}")
            raise ConnectorError(text) from exc
        except Exception as exc:
            raise ConnectorError(f"нет соединения с {host}:{port}: {exc}") from exc
        return client, warnings

    def _remote_files(self, sftp) -> list[tuple[str, int]]:
        remote = str(self.opt("remote_dir", "."))
        masks = self.opt_list("pattern") or ["*.xlsx"]
        out = []
        for attr in sftp.listdir_attr(remote):
            mode = getattr(attr, "st_mode", None)
            if mode is not None and not stat.S_ISREG(mode):
                continue
            if fileutil.match_masks(attr.filename, masks):
                out.append((attr.filename, int(getattr(attr, "st_size", 0) or 0)))
        return sorted(out)

    # ---------------------------------------------------------------- api
    def _test(self) -> dict:
        client, warnings = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                files = self._remote_files(sftp)
            finally:
                sftp.close()
        finally:
            client.close()
        detail = (f"соединение установлено: {self.opt('host')}:{self.opt_int('port', 22)}, "
                  f"файлов по маске в {self.opt('remote_dir', '.')}: {len(files)}")
        res = {"ok": True, "detail": detail}
        if warnings:
            res["detail"] = detail + ". " + " ".join(warnings)
            res["warnings"] = warnings
        return res

    def _pull(self, dest_dir: str) -> list[dict]:
        client, warnings = self._connect()
        self.last_notes.extend(warnings)
        try:
            sftp = client.open_sftp()
            try:
                remote = str(self.opt("remote_dir", "."))
                files = self._remote_files(sftp)
                if not files:
                    raise ConnectorError(f"файлы {self.opt_list('pattern') or ['*.xlsx']} "
                                         f"не найдены в {remote}")
                limit = self.opt_int("max_file_mb", 200) * 2**20
                out: list[dict] = []
                used: set = set()
                for name, size in files:
                    if size > limit:
                        self.last_notes.append(f"{name}: больше {limit // 2**20} МБ, пропущен")
                        continue
                    buf = io.BytesIO()
                    sftp.getfo(posixpath.join(remote, name), buf)
                    out.extend(self._store_file(dest_dir, name, buf.getvalue(),
                                                masks=fileutil.DEFAULT_MASKS,
                                                origin=f"sftp://{self.opt('host')}"
                                                       f"{posixpath.join(remote, name)}",
                                                used=used))
                return out
            finally:
                sftp.close()
        finally:
            client.close()
