# -*- coding: utf-8 -*-
"""Реестр коннекторов источников данных.

Добавление нового коннектора: унаследоваться от Connector, зарегистрировать в TYPES.
"""
from __future__ import annotations

from .base import Connector, ConnectorError
from .folder import FolderConnector
from .sftp import SftpConnector
from .database import DatabaseConnector
from .sap_odata import SapODataConnector

TYPES = {
    "folder": FolderConnector,
    "sftp": SftpConnector,
    "database": DatabaseConnector,
    "sap_odata": SapODataConnector,
}


def list_connectors(cfg: dict) -> list[Connector]:
    out = []
    for cid, ccfg in (cfg.get("connectors") or {}).items():
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
