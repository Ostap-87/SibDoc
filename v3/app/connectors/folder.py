# -*- coding: utf-8 -*-
"""Коннектор «локальная папка»: забирает новые Excel/CSV/XML из указанной папки
(в т. ч. сетевой, смонтированной на сервере — сюда удобно класть файлы китайского
брокера). Архивы .zip распаковываются (с защитой от zip-slip и zip-бомб), CSV в
GBK/cp1251 перекодируются в UTF-8.

config/system.yaml:
  connectors:
    incoming:
      type: folder
      enabled: false
      label: Папка с выгрузками
      path: /data/incoming          # путь на сервере
      pattern: "*.xlsx"             # маска или список масок: ["*.xlsx", "*.csv", "*.zip"]
      slots: [wide_table, purchase] # слот по началу имени файла
      slot_rules:                   # слот по regex (имя файла)
        - {pattern: '(?i)(报关单|customs.?dec|cn_dec)', slot: cn_dec}
"""
from __future__ import annotations

import glob
import os

from . import fileutil
from .base import Connector, ConnectorError, P, SLOT_PARAMS, STAGE_READY


class FolderConnector(Connector):
    type = "folder"
    title = "Папка на сервере (выгрузки, файлы брокера)"
    stage = STAGE_READY
    params = [
        P("path", "Папка на сервере", required=True, example="/data/incoming/broker_cn"),
        P("pattern", "Маска файлов", example='"*.xlsx" или ["*.xlsx", "*.csv", "*.xml", "*.zip"]'),
        P("unzip", "Распаковывать .zip", example="true"),
    ] + SLOT_PARAMS

    def _test(self) -> dict:
        path = self.opt("path", "")
        if not os.path.isdir(path):
            return {"ok": False, "detail": f"папка не найдена: {path}"}
        n = len(self._files())
        return {"ok": True, "detail": f"папка доступна, файлов по маске: {n}"}

    def _pull(self, dest_dir: str) -> list[dict]:
        files = self._files()
        if not files:
            raise ConnectorError(f"нет файлов в {self.opt('path')}")
        out: list[dict] = []
        used: set = set()
        for src in files:
            with open(src, "rb") as fh:
                data = fh.read()
            out.extend(self._store_file(dest_dir, os.path.basename(src), data,
                                        masks=fileutil.DEFAULT_MASKS, origin=src, used=used))
        return out

    def _files(self) -> list[str]:
        path = self.opt("path", "")
        patterns = self.opt_list("pattern") or ["*.xlsx"]
        found: list[str] = []
        for pat in patterns:
            found += glob.glob(os.path.join(path, str(pat)))
        found = sorted({f for f in found if os.path.isfile(f)})
        if not found and patterns == ["*.xlsx"] and not self.cfg.get("pattern"):
            found = sorted(glob.glob(os.path.join(path, "*.xlsx")))
            found += sorted(glob.glob(os.path.join(path, "*.csv")))
        return found
