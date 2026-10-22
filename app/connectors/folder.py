# -*- coding: utf-8 -*-
"""Коннектор «локальная папка»: забирает новые Excel/CSV из указанной папки.

config/system.yaml:
  connectors:
    incoming:
      type: folder
      enabled: true
      label: Папка с выгрузками
      path: /data/incoming          # путь на сервере
      pattern: "*.xlsx"             # маска файлов
      slots: [wide_table, purchase] # маппинг по имени файла
"""
from __future__ import annotations

import glob
import os

from .base import Connector, ConnectorError


class FolderConnector(Connector):
    type = "folder"

    def test(self) -> dict:
        path = self.cfg.get("path", "")
        if not path:
            return {"ok": False, "detail": "не задан path"}
        if not os.path.isdir(path):
            return {"ok": False, "detail": f"папка не найдена: {path}"}
        n = len(self._files())
        return {"ok": True, "detail": f"папка доступна, файлов по маске: {n}"}

    def pull(self, dest_dir: str) -> list[dict]:
        files = self._files()
        if not files:
            raise ConnectorError(f"нет файлов в {self.cfg.get('path')}")
        out = []
        for src in files:
            with open(src, "rb") as fh:
                data = fh.read()
            path = self._write_bytes(dest_dir, os.path.basename(src), data)
            out.append({"slot": self._slot_for_name(src, self.cfg.get("slots") or []),
                        "file": os.path.basename(src), "path": path, "rows": None})
        return out

    def _files(self) -> list[str]:
        path = self.cfg.get("path", "")
        pattern = self.cfg.get("pattern", "*.xlsx")
        found = sorted(glob.glob(os.path.join(path, pattern)))
        if not found and pattern != "*.*":
            found = sorted(glob.glob(os.path.join(path, "*.xlsx")))
            found += sorted(glob.glob(os.path.join(path, "*.csv")))
        return found
