# -*- coding: utf-8 -*-
"""Каркас коннектора второй очереди (stage = "needs_access") и мост «папка выгрузок».

Пока доступа к системе нет:
  * test() -> {"ok": False, "detail": "Задел: нужен доступ — <что получить и у кого>"};
  * pull() -> ConnectorError с тем же текстом.
Когда параметры доступа заполнены, а протокол обмена ещё не реализован,
test()/pull() честно говорят «Каркас: ... уточнить у владельца системы».

Мост до появления доступа (Коннекторы/банк: полноценный режим «работает через выгрузки»):
export_dir — папка, куда сотрудник или программа владельца системы сохраняет выгрузки
(Excel/CSV/XML/TXT/zip). Если она задана, коннектор забирает файлы как «папка» (folder.py)
с настройками по умолчанию для своей системы (export_defaults: маски, правила слотов) и
определяет слот по СОДЕРЖИМОМУ файла (sniff.py): выписка банка, EnterpriseData, ДТ, 报关单…
"""
from __future__ import annotations

import copy

from . import sniff
from .base import Connector, ConnectorError, P, SLOT_PARAMS, STAGE_NEEDS_ACCESS
from .folder import FolderConnector

EXPORT_PARAMS = [
    P("export_dir", "Пока нет доступа: папка с выгрузками из системы",
      example="/data/incoming/export"),
    P("pattern", "Маска файлов в папке выгрузок", example='["*.xlsx", "*.xml", "*.zip"]'),
    P("detect_content", "Определять слот по содержимому файла", example="true"),
] + SLOT_PARAMS


class ExportBridge:
    """Примесь: режим «папка выгрузок» (export_dir) для коннекторов без прямого доступа."""

    export_defaults: dict = {}          # pattern, slot_rules, slot_exts, zip_masks … для своей системы
    content_slots: tuple = ()           # какие слоты определять по содержимому (пусто — не определять)
    slot_aliases: dict = {}             # слот из sniff -> слот из настроек (ЭТРАН: transport -> slot)

    def _export_mode(self) -> bool:
        return bool(self.opt("export_dir"))

    def _folder(self) -> FolderConnector:
        cfg = copy.deepcopy(self.export_defaults)
        cfg.update({k: v for k, v in self.cfg.items() if v not in (None, "", [], {})})
        cfg["path"] = self.cfg.get("export_dir")
        return FolderConnector(self.id, cfg)

    def _export_test(self) -> dict:
        res = self._folder()._test()
        res["detail"] = f"режим папки выгрузок: {res['detail']}; прямой доступ — задел"
        return res

    def _export_pull(self, dest_dir: str) -> list[dict]:
        folder = self._folder()
        try:
            items = folder._pull(dest_dir)
        finally:
            self.last_notes.extend(folder.last_notes)
        return self._detect_slots(items)

    def _detect_slots(self, items: list[dict]) -> list[dict]:
        if not self.content_slots or not self.opt_bool("detect_content", True):
            return items
        out = []
        allowed = [self.slot_aliases.get(s, s) for s in self.content_slots]
        for it in items:
            try:
                with open(it["path"], "rb") as fh:
                    data = fh.read()
                slots = [self.slot_aliases.get(s, s) for s in sniff.slots_of(it.get("file") or it["path"], data,
                                                                              list(self.content_slots))]
            except OSError:
                slots = []
            slots = [s for s in slots if s in allowed]
            if not slots:
                out.append(it)
                continue
            for s in slots:
                out.append(dict(it, slot=s, detected="по содержимому"))
        return out


class StubConnector(ExportBridge, Connector):
    stage = STAGE_NEEDS_ACCESS
    access_hint = ""
    protocol_note = "протокол обмена уточнить у владельца системы"
    implemented = False
    verified = "none"

    def _gate(self) -> str | None:
        if self._export_mode():
            return None
        return super()._gate()

    def _offline_targets(self) -> list[str]:
        # SibDoc3: закрытый контур — режим папки выгрузок в сеть не ходит
        return [] if self._export_mode() else super()._offline_targets()

    def _skeleton_text(self) -> str:
        return f"Каркас: параметры доступа заполнены, но обмен ещё не реализован — {self.protocol_note}"

    def _test(self) -> dict:
        if self._export_mode():
            return self._export_test()
        return {"ok": False, "detail": self._skeleton_text()}

    def _pull(self, dest_dir: str) -> list[dict]:
        if self._export_mode():
            return self._export_pull(dest_dir)
        raise ConnectorError(self._skeleton_text())
