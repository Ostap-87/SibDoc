# -*- coding: utf-8 -*-
"""Базовый класс коннектора источников данных.

Каждый коннектор умеет:
  test()                 — проверить доступность/доступ (без загрузки данных)
  pull(dest_dir)         — забрать свежие файлы данных в dest_dir
  public_dict()          — описание для интерфейса (без секретов)
"""
from __future__ import annotations

import os


class ConnectorError(Exception):
    pass


class Connector:
    type = "base"

    def __init__(self, cid: str, cfg: dict):
        self.id = cid
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled"))
        self.label = cfg.get("label") or cid

    # ------------------------------------------------------------------ api
    def test(self) -> dict:
        """-> {"ok": bool, "detail": str}"""
        raise NotImplementedError

    def pull(self, dest_dir: str) -> list[dict]:
        """-> [{"slot": str|None, "file": filename, "path": abs_path, "rows": int|None}]"""
        raise NotImplementedError

    def public_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "label": self.label,
                "enabled": self.enabled}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _slot_for_name(name: str, slots: list[str]) -> str | None:
        """Слот по имени файла: 'wide_table_2026-10-06.xlsx' -> 'wide_table'."""
        stem = os.path.splitext(os.path.basename(name))[0].lower()
        for s in slots or []:
            if stem == s.lower() or stem.startswith(s.lower() + "_") \
               or stem.startswith(s.lower() + "-") or stem.startswith(s.lower() + " "):
                return s
        return None

    @staticmethod
    def _write_bytes(dest_dir: str, filename: str, content: bytes) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        # защита от path traversal
        safe = os.path.basename(filename)
        path = os.path.join(dest_dir, safe)
        with open(path, "wb") as fh:
            fh.write(content)
        return path
