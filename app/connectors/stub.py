# -*- coding: utf-8 -*-
"""Каркас коннектора второй очереди (stage = "needs_access").

Пока доступа к системе нет:
  * test() -> {"ok": False, "detail": "Задел: нужен доступ — <что получить и у кого>"};
  * pull() -> ConnectorError с тем же текстом.
Когда параметры доступа заполнены, а протокол обмена ещё не реализован,
test()/pull() честно говорят «Каркас: ... уточнить у владельца системы».

Мост до появления доступа: export_dir — папка, куда сотрудник или программа
владельца системы сохраняет выгрузки (Excel/CSV/XML/zip). Если она задана,
коннектор работает как «папка» (см. folder.py), прямой доступ остаётся заделом.
"""
from __future__ import annotations

from .base import Connector, ConnectorError, P, SLOT_PARAMS, STAGE_NEEDS_ACCESS
from .folder import FolderConnector

EXPORT_PARAMS = [
    P("export_dir", "Пока нет доступа: папка с выгрузками из системы",
      example="/data/incoming/export"),
    P("pattern", "Маска файлов в папке выгрузок", example='["*.xlsx", "*.xml", "*.zip"]'),
] + SLOT_PARAMS


class StubConnector(Connector):
    stage = STAGE_NEEDS_ACCESS
    access_hint = ""
    protocol_note = "протокол обмена уточнить у владельца системы"

    def _export_mode(self) -> bool:
        return bool(self.opt("export_dir"))

    def _gate(self) -> str | None:
        if self._export_mode():
            return None
        return super()._gate()

    def _folder(self) -> FolderConnector:
        cfg = dict(self.cfg)
        cfg["path"] = self.cfg.get("export_dir")
        return FolderConnector(self.id, cfg)

    def _skeleton_text(self) -> str:
        return f"Каркас: параметры доступа заполнены, но обмен ещё не реализован — {self.protocol_note}"

    def _test(self) -> dict:
        if self._export_mode():
            res = self._folder()._test()
            res["detail"] = f"режим папки выгрузок: {res['detail']}; прямой доступ — задел"
            return res
        return {"ok": False, "detail": self._skeleton_text()}

    def _pull(self, dest_dir: str) -> list[dict]:
        if self._export_mode():
            folder = self._folder()
            try:
                return folder._pull(dest_dir)
            finally:
                self.last_notes.extend(folder.last_notes)
        raise ConnectorError(self._skeleton_text())
