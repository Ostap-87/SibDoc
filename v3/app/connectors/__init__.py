# -*- coding: utf-8 -*-
"""Реестр коннекторов источников данных.

Первая очередь (stage "ready", работают по-настоящему): folder, sftp, imap, rest,
database, sap_odata. Вторая очередь (stage "needs_access" — прямой доступ ещё не выдан):
onec_odata, kingdee, yonyou — обмен реализован и проверен на эмуляторе (tests/emulators.py);
bank_fx_control, cn_single_window, etran — работают через выгрузки в папку (export_dir),
прямой обмен — задел. Статус экземпляра — Connector.status(): works | via_exports |
needs_access (что нужно и у кого). Все коннекторы по умолчанию выключены (enabled: false).

Добавление нового коннектора: унаследоваться от Connector (или RestConnector /
StubConnector), задать type/title/stage/params, реализовать _test()/_pull(),
зарегистрировать в TYPES.
"""
from __future__ import annotations

from .base import Connector, ConnectorError, KNOWN_SLOTS, STAGE_NEEDS_ACCESS, STAGE_READY
from .folder import FolderConnector
from .sftp import SftpConnector
from .database import DatabaseConnector
from .sap_odata import SapODataConnector
from .imap_mail import ImapMailConnector
from .rest_api import RestConnector
from .onec_odata import OneCODataConnector
from .kingdee import KingdeeConnector
from .yonyou import YonyouConnector
from .etran import EtranConnector
from .cn_single_window import CnSingleWindowConnector
from .bank_fx import BankFxControlConnector

TYPES = {
    # первая очередь
    "folder": FolderConnector,
    "sftp": SftpConnector,
    "imap": ImapMailConnector,
    "rest": RestConnector,
    "database": DatabaseConnector,
    "sap_odata": SapODataConnector,
    # вторая очередь (задел)
    "onec_odata": OneCODataConnector,
    "kingdee": KingdeeConnector,
    "yonyou": YonyouConnector,
    "etran": EtranConnector,
    "cn_single_window": CnSingleWindowConnector,
    "bank_fx_control": BankFxControlConnector,
}

__all__ = ["Connector", "ConnectorError", "TYPES", "KNOWN_SLOTS", "STAGE_READY",
           "STAGE_NEEDS_ACCESS", "list_connectors", "get_connector", "catalog"]


def list_connectors(cfg: dict) -> list[Connector]:
    out = []
    for cid, ccfg in (cfg.get("connectors") or {}).items():
        if not isinstance(ccfg, dict):
            continue
        cls = TYPES.get(ccfg.get("type"))
        if cls is None:
            continue
        out.append(cls(cid, ccfg))
    return out


def get_connector(cfg: dict, cid: str) -> Connector | None:
    for c in list_connectors(cfg):
        if c.id == cid:
            return c
    return None


def catalog() -> list[dict]:
    """Какие коннекторы бывают: [{type, title, stage, stage_title, access_hint, params}]."""
    return [cls.catalog_entry() for cls in TYPES.values()]
