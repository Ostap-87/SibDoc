# -*- coding: utf-8 -*-
"""Коннектор БД через SQLAlchemy — прямой забор данных из FineBI/SAP-views.

Поддерживаются все диалекты, для которых установлен драйвер:
  mssql+pyodbc (MS SQL), oracle+oracledb (Oracle), postgresql+psycopg (PostgreSQL)...

  connectors:
    finebi_db:
      type: database
      enabled: false
      label: БД FineBI
      url: "mssql+pyodbc://USER:PASS@server:1433/DB?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes"
      queries:
        wide_table: "SELECT * FROM v_wide_table"
        purchase:   "SELECT * FROM v_purchase"
      timeout_sec: 300
"""
from __future__ import annotations

import csv

from .base import Connector, ConnectorError


class DatabaseConnector(Connector):
    type = "database"

    def _engine(self):
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("SQLAlchemy не установлена") from exc
        url = self.cfg.get("url", "")
        if not url:
            raise ConnectorError("не задан url подключения")
        try:
            return create_engine(url, connect_args={"timeout": int(self.cfg.get("timeout_sec", 300))}
                                 if url.startswith("sqlite") else {})
        except Exception as exc:
            raise ConnectorError(f"ошибка URL/драйвера: {exc}") from exc

    def test(self) -> dict:
        try:
            engine = self._engine()
            with engine.connect() as conn:
                from sqlalchemy import text
                conn.execute(text("SELECT 1"))
            return {"ok": True, "detail": "подключение успешно (SELECT 1)"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:300]}

    def pull(self, dest_dir: str) -> list[dict]:
        queries = self.cfg.get("queries") or {}
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
        import os
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, os.path.basename(fname))
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow(list(r))
        return path
