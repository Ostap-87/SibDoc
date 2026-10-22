# -*- coding: utf-8 -*-
"""Коннектор SAP через OData/REST (без проприетарного RFC SDK).

Работает с любым SAP, отдающим OData (S/4HANA, Gateway, CPI, BTP):
  * OData v2: записи в d.results, следующая страница — d.__next;
  * OData v4: записи в value, следующая страница — @odata.nextLink
    (v3/JSON light: odata.nextLink — тоже поддерживается).
Для on-prem SAP внутри корпконтура — basic, для облачного — OAuth2 (client credentials).

  connectors:
    sap_po:
      type: sap_odata
      enabled: false
      label: SAP S/4HANA (OData)
      base_url: "https://sap.company.ru:44300/sap/opu/odata/sap/API_PURCHASEORDER_0001"
      # v4: "https://sap.company.ru/sap/opu/odata4/sap/api_purchaseorder_2/srvd_a2x/sap/purchaseorder/0001"
      entity: "A_PurchaseOrderItem"
      params: {"$top": "50000", "$filter": "Plant eq '1020'"}
      auth: basic            # basic | oauth2
      username: svc_sibdoc
      password_env: SIBDOC_SAP_PASSWORD
      # для oauth2:
      # token_url: "https://<subaccount>.authentication.eu10.hana.ondemand.com/oauth/token"
      # client_id: "sb-..."
      # client_secret_env: SIBDOC_SAP_CLIENT_SECRET
      verify_tls: true
      slots: [purchase]      # файл сохраняется как <slot>.csv
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from .base import Connector, ConnectorError, P, STAGE_READY, TLS_PARAMS


def odata_page(data) -> tuple[list, str | None]:
    """Ответ OData (v2/v3/v4) -> (записи, ссылка на следующую страницу)."""
    if isinstance(data, list):
        return data, None
    if not isinstance(data, dict):
        return [data], None
    if "d" in data:                                   # v2 (и v1 verbose)
        payload = data["d"]
        if isinstance(payload, list):
            return payload, None
        if isinstance(payload, dict):
            if "results" in payload:
                return list(payload.get("results") or []), payload.get("__next")
            return [payload], None
    if "value" in data and isinstance(data["value"], list):   # v4 / v3 JSON light
        return data["value"], data.get("@odata.nextLink") or data.get("odata.nextLink")
    return [data], None


class SapODataConnector(Connector):
    type = "sap_odata"
    title = "SAP (OData v2/v4)"
    stage = STAGE_READY
    params = [
        P("base_url", "Адрес OData-сервиса", required=True,
          example="https://sap.company.ru:44300/sap/opu/odata/sap/API_PURCHASEORDER_0001"),
        P("entity", "Набор сущностей (EntitySet)", required=True, example="A_PurchaseOrderItem"),
        P("params", "Параметры запроса", example='{"$top": "50000", "$filter": "Plant eq \'1020\'"}'),
        P("auth", "Авторизация", example="basic | oauth2"),
        P("username", "Логин (basic)", example="svc_sibdoc"),
        P("password", "Пароль (basic)", secret=True, example="password_env: SIBDOC_SAP_PASSWORD"),
        P("token_url", "Адрес выдачи токена (oauth2)", example="https://.../oauth/token"),
        P("client_id", "client_id (oauth2)"),
        P("client_secret", "client_secret (oauth2)", secret=True,
          example="client_secret_env: SIBDOC_SAP_CLIENT_SECRET"),
        P("scope", "scope (oauth2, если нужен)"),
        P("slots", "Слот (файл <slot>.csv)", example="[purchase]"),
    ] + TLS_PARAMS

    def _auth_mode(self) -> str:
        return str(self.opt("auth", "basic")).lower()

    def _extra_missing(self) -> list[str]:
        if self._auth_mode() == "oauth2":
            return [k for k in ("token_url", "client_id") if not self.opt(k)] + \
                   ([] if self.secret("client_secret") else ["client_secret"])
        return [k for k, ok in (("username", self.opt("username")),
                                ("password", self.secret("password"))) if not ok]

    # ---------------------------------------------------------------- auth
    def _session(self):
        requests = self._requests()
        s = requests.Session()
        s.verify = self._verify()
        s.headers.update({"Accept": "application/json"})
        if self._auth_mode() == "oauth2":
            s.headers.update({"Authorization": f"Bearer {self._token(requests)}"})
        else:
            s.auth = (self.opt("username", ""), self.secret("password"))
        return requests, s

    def _token(self, requests) -> str:
        data = {"grant_type": "client_credentials"}
        if self.opt("scope"):
            data["scope"] = self.opt("scope")
        try:
            resp = requests.post(self.opt("token_url"), data=data,
                                 auth=(self.opt("client_id", ""), self.secret("client_secret")),
                                 timeout=30, verify=self._verify())
            resp.raise_for_status()
            return resp.json()["access_token"]
        except Exception as exc:
            raise ConnectorError(f"OAuth2: токен не получен: {exc}") from exc

    # ---------------------------------------------------------------- api
    def _test(self) -> dict:
        _, s = self._session()
        url = str(self.opt("base_url", "")).rstrip("/") + "/$metadata"
        resp = s.get(url, timeout=30, headers={"Accept": "application/xml"})
        if resp.status_code >= 400:
            return {"ok": False, "detail": f"HTTP {resp.status_code} на $metadata: {resp.text[:200]}"}
        m = re.search(r'Version="(\d\.\d)"', resp.text or "")
        ver = {"4.0": "OData v4", "4.01": "OData v4"}.get(m.group(1), "OData v2") if m else "OData"
        return {"ok": True, "detail": f"{ver}-сервис доступен (HTTP {resp.status_code})"}

    def _pull(self, dest_dir: str) -> list[dict]:
        base = str(self.opt("base_url", "")).rstrip("/")
        entity = str(self.opt("entity", ""))
        _, s = self._session()
        rows_all: list = []
        url: str | None = f"{base}/{entity}"
        params = dict(self.opt("params") or {})
        seen_urls = set()
        pages = 0
        while url and pages < self.opt_int("max_pages", 10000):
            seen_urls.add(url)
            resp = s.get(url, params=params or None, timeout=self.opt_int("timeout_sec", 120))
            if resp.status_code >= 400:
                raise ConnectorError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                data = resp.json()
            except ValueError as exc:
                raise ConnectorError("ответ OData не JSON (проверьте base_url/entity)") from exc
            batch, nxt = odata_page(data)
            rows_all.extend(batch)
            pages += 1
            url = urljoin(url, nxt) if nxt else None
            if url in seen_urls:
                break
            params = {}   # ссылка на следующую страницу уже содержит параметры
        if not rows_all:
            raise ConnectorError("OData вернул 0 строк — файл не перезаписан")
        slot = (self.opt_list("slots") or [self.opt("slot") or entity])[0]
        fname = f"{slot}.csv"
        path, n = self._write_records_csv(dest_dir, fname, rows_all)
        return [{"slot": slot, "file": fname, "path": path, "rows": n}]
