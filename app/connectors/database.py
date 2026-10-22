# -*- coding: utf-8 -*-
"""Коннектор БД через SQLAlchemy — прямой забор данных из FineBI/SAP-views.

Поддерживаются все диалекты, для которых установлен драйвер:
  mssql+pyodbc (MS SQL), oracle+oracledb (Oracle), postgresql+psycopg (PostgreSQL)...

Строка подключения содержит пароль, поэтому считается секретом: задайте её
целиком в переменной окружения (url_env) или подставьте пароль через ${VAR}
(спецсимволы пароля в URL кодируются: @ -> %40, : -> %3A).

  connectors:
    finebi_db:
      type: database
      enabled: false
      label: БД FineBI
      url_env: SIBDOC_FINEBI_DB_URL
      # url: "mssql+pyodbc://svc:${SIBDOC_DB_PASSWORD}@server:1433/DB?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes"
      queries:
        wide_table: "SELECT * FROM v_wide_table"
        purchase:   "SELECT * FROM v_purchase"
      timeout_sec: 300
"""
from __future__ import annotations

import csv
import os

from .base import Connector, ConnectorError, P, STAGE_READY


class DatabaseConnector(Connector):
    type = "database"
    title = "База данных (SQL: FineBI, SAP-views)"
    stage = STAGE_READY
    params = [
        P("url", "Строка подключения SQLAlchemy", required=True, secret=True,
          example="url_env: SIBDOC_FINEBI_DB_URL"),
        P("queries", "Запросы по слотам", required=True,
          example='{wide_table: "SELECT * FROM v_wide_table"}'),
        P("timeout_sec", "Тайм-аут, сек", example="300"),
    ]

    def _engine(self):
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("SQLAlchemy не установлена (pip install sqlalchemy + драйвер БД)") from exc
        url = self.secret("url")
        if not url:
            raise ConnectorError("не задан url подключения (url_env)")
        try:
            return create_engine(url, connect_args={"timeout": self.opt_int("timeout_sec", 300)}
                                 if url.startswith("sqlite") else {})
        except Exception as exc:
            raise ConnectorError(f"ошибка URL/драйвера: {exc}") from exc

    def _test(self) -> dict:
        engine = self._engine()
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "detail": "подключение успешно (SELECT 1)"}

    def _pull(self, dest_dir: str) -> list[dict]:
        queries = self.opt("queries") or {}
        if not queries:
            raise ConnectorError("не заданы queries: {slot: SQL}")
        engine = self._engine()
        out = []
        from sqlalchemy import text
        for slot, sql in queries.items():
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                cols = list(result.keys())
                rows = result.fetchall()
            fname = f"{slot}.csv"
            path = self._csv(dest_dir, fname, cols, rows)
            out.append({"slot": slot, "file": fname, "path": path, "rows": len(rows)})
        return out

    @staticmethod
    def _csv(dest_dir: str, fname: str, cols: list, rows) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, os.path.basename(fname))
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow(list(r))
        return path
