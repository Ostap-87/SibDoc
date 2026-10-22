# -*- coding: utf-8 -*-
"""Коннектор SFTP (paramiko) — защищённый приём файлов из корпоративного контура.

  connectors:
    sftp_in:
      type: sftp
      enabled: false
      label: SFTP Сибур
      host: sftp.company.ru
      port: 22
      username: svc_sibdoc
      password: ""            # или key_file (рекомендуется ключ)
      key_file: ""
      remote_dir: /uploads/sibdoc
      pattern: "*.xlsx"
      slots: [wide_table, purchase]
"""
from __future__ import annotations

import fnmatch
import os

from .base import Connector, ConnectorError


class SftpConnector(Connector):
    type = "sftp"

    def _connect(self):
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("paramiko не установлен") from exc
        host = self.cfg.get("host", "")
        if not host:
            raise ConnectorError("не задан host")
        transport_args = dict(
            hostname=host,
            port=int(self.cfg.get("port", 22)),
            username=self.cfg.get("username", ""),
            timeout=15,
        )
        key_file = self.cfg.get("key_file") or None
        if key_file:
            transport_args["key_filename"] = os.path.expanduser(key_file)
        else:
            transport_args["password"] = self.cfg.get("password", "")
        return paramiko, paramiko.SSHClient(), transport_args

    def test(self) -> dict:
        try:
            paramiko, client, args = self._connect()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(**args)
            sftp = client.open_sftp()
            remote = self.cfg.get("remote_dir", ".")
            sftp.listdir(remote)
            sftp.close(); client.close()
            return {"ok": True, "detail": f"соединение установлено: {args['hostname']}:{args['port']}"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:300]}

    def pull(self, dest_dir: str) -> list[dict]:
        paramiko, client, args = self._connect()
        try:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(**args)
            sftp = client.open_sftp()
            remote = self.cfg.get("remote_dir", ".")
            pattern = self.cfg.get("pattern", "*.xlsx")
            names = [n for n in sftp.listdir(remote) if fnmatch.fnmatch(n, pattern)]
            if not names:
                raise ConnectorError(f"файлы {pattern} не найдены в {remote}")
            out = []
            for name in sorted(names):
                tmp = os.path.join(dest_dir, ".sftp_tmp_" + os.path.basename(name))
                os.makedirs(dest_dir, exist_ok=True)
                sftp.get(os.path.join(remote, name), tmp)
                with open(tmp, "rb") as fh:
                    data = fh.read()
                os.remove(tmp)
                path = self._write_bytes(dest_dir, name, data)
                out.append({"slot": self._slot_for_name(name, self.cfg.get("slots") or []),
                            "file": name, "path": path, "rows": None})
            sftp.close(); client.close()
            return out
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(str(exc)[:300]) from exc
