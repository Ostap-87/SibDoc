# -*- coding: utf-8 -*-
"""Коннектор SAP через OData/REST (без проприетарного RFC SDK).

Работает с любым SAP, отдающим OData (S/4HANA, Gateway, CPI).
Для on-prem SAP внутри корпконтура — прямой URL, для облачного — OAuth2 (client credentials).

  connectors:
    sap_po:
      type: sap_odata
      enabled: false
      label: SAP S/4HANA (OData)
      base_url: "https://sap.company.ru:44300/sap/opu/odata/sap/API_PURCHASEORDER_0001"
      entity: "A_PurchaseOrderItem"
      params: {"$top": "50000", "$filter": "Plant eq '1020'"}
      auth: basic            # basic | oauth2
      username: ""
      password: ""
      # для oauth2:
      token_url: ""
      client_id: ""
      client_secret: ""
      verify_tls: true
      slots: [purchase]
"""
from __future__ import annotations

import csv
import os

from .base import Connector, ConnectorError


class SapODataConnector(Connector):
    type = "sap_odata"

    # ---------------------------------------------------------------- auth
    def _session(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("requests не установлен") from exc
        s = requests.Session()
        s.verify = bool(self.cfg.get("verify_tls", True))
        s.headers.update({"Accept": "application/json"})
        auth = self.cfg.get("auth", "basic")
        if auth == "oauth2":
            token = self._token(requests)
            s.headers.update({"Authorization": f"Bearer {token}"})
        else:
            s.auth = (self.cfg.get("username", ""), self.cfg.get("password", ""))
        return requests, s

    def _token(self, requests) -> str:
        try:
            resp = requests.post(
                self.cfg["token_url"],
                data={"grant_type": "client_credentials"},
                auth=(self.cfg.get("client_id", ""), self.cfg.get("client_secret", "")),
                timeout=30, verify=bool(self.cfg.get("verify_tls", True)),
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
        except Exception as exc:
            raise ConnectorError(f"OAuth2: {exc}") from exc

    # ---------------------------------------------------------------- api
    def test(self) -> dict:
        try:
            requests, s = self._session()
            url = self.cfg.get("base_url", "").rstrip("/") + "/$metadata"
            resp = s.get(url, timeout=30)
            resp.raise_for_status()
            return {"ok": True, "detail": f"OData сервис доступен (HTTP {resp.status_code})"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:300]}

    def pull(self, dest_dir: str) -> list[dict]:
        base = self.cfg.get("base_url", "").rstrip("/")
        entity = self.cfg.get("entity", "")
        if not base or not entity:
            raise ConnectorError("не заданы base_url/entity")
        requests, s = self._session()
        rows_all: list[dict] = []
        url = f"{base}/{entity}"
        params = dict(self.cfg.get("params") or {})
        while url:
            resp = s.get(url, params=params if params else None, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            payload = data.get("d", data)
            if isinstance(payload, dict) and "results" in payload:
                rows_all.extend(payload["results"])
            elif isinstance(payload, list):
                rows_all.extend(payload)
            else:
                rows_all.append(payload)
            nxt = payload.get("__next") if isinstance(payload, dict) else None
            url = (base.split("/sap/opu")[0] + nxt) if nxt and nxt.startswith("/") else (nxt or None)
            params = {}
        if not rows_all:
            raise ConnectorError("OData вернул 0 строк")
        cols = sorted({k for r in rows_all for k in r.keys()
                       if not k.startswith("__")})
        slot = (self.cfg.get("slots") or [entity])[0]
        fname = f"{slot}.csv"
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, fname)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows_all:
                w.writerow(r)
        return [{"slot": slot, "file": fname, "path": path, "rows": len(rows_all)}]
