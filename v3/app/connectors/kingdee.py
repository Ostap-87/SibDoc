# -*- coding: utf-8 -*-
"""Kingdee K3Cloud / 金蝶云星空 WebAPI (задел; реализовано на базе rest).

Эндпоинты (POST, JSON; сессия — cookie после входа):
  вход:    <server_url>/Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc
           тело {"acctID": "<ID набора данных 账套>", "username": "...", "password": "...",
                 "lcid": 2052}; успех — LoginResultType = 1 (или IsSuccessByAPI = true)
  запрос:  <server_url>/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc
           тело {"data": {"FormId": "PUR_PurchaseOrder", "FieldKeys": "FBillNo,FDate,...",
                          "FilterString": "", "OrderString": "", "TopRowCount": 0,
                          "StartRow": 0, "Limit": 2000}}
           ответ — массив строк-массивов в порядке FieldKeys; ошибка — [[{"Result":
           {"ResponseStatus": {"IsSuccess": false, "Errors": [...]}}}]]
Формат тела зависит от версии K3Cloud (есть вариант {"parameters": [...]} и вход по
AppID/AppSecret — LoginByAppSecret): уточнить у владельца системы. Пути сервисов
переопределяются параметрами login_service / query_service.

  connectors:
    kingdee:
      type: kingdee
      enabled: false
      label: Kingdee 金蝶云星空
      server_url: "https://erp.company.cn/k3cloud"
      acct_id: ""                      # ID набора данных (账套ID)
      username: ""
      password_env: SIBDOC_KINGDEE_PASSWORD
      lcid: 2052                       # 2052 — китайский (упрощённый)
      form_id: PUR_PurchaseOrder
      field_keys: "FBillNo,FDate,FSupplierId.FName,FMaterialId.FNumber,FQty,FPrice"
      filter_string: "FDocumentStatus='C'"
      page_size: 2000
      slot: purchase
"""
from __future__ import annotations

from .base import ConnectorError, P, STAGE_NEEDS_ACCESS, TLS_PARAMS
from .rest_api import RestConnector

LOGIN_SERVICE = "Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc"
QUERY_SERVICE = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"


class KingdeeConnector(RestConnector):
    type = "kingdee"
    title = "Kingdee K3Cloud (金蝶云星空) WebAPI"
    stage = STAGE_NEEDS_ACCESS
    access_hint = ("у администратора Kingdee (金蝶云星空): адрес сервера K3Cloud, ID набора данных "
                   "(账套ID / acctID), пользователь с правом WebAPI на нужный объект, FormId и поля "
                   "FieldKeys (например закупочный заказ PUR_PurchaseOrder)")
    params = [
        P("server_url", "Адрес K3Cloud", required=True, example="https://erp.company.cn/k3cloud"),
        P("acct_id", "ID набора данных (账套ID)", required=True, example="5f2b8c..."),
        P("username", "Пользователь", required=True),
        P("password", "Пароль", required=True, secret=True,
          example="password_env: SIBDOC_KINGDEE_PASSWORD"),
        P("lcid", "Язык (2052 — китайский)", example="2052"),
        P("form_id", "FormId объекта (表单ID)", required=True, example="PUR_PurchaseOrder"),
        P("field_keys", "Поля (FieldKeys)", required=True, example="FBillNo,FDate,FQty,FPrice"),
        P("filter_string", "Фильтр (FilterString)", example="FDocumentStatus='C'"),
        P("page_size", "Строк за запрос (Limit)", example="2000"),
        P("slot", "Слот (файл <slot>.csv)", required=True, example="purchase"),
    ] + TLS_PARAMS
    defaults = {"auth": "none", "method": "POST", "page_size": 2000}

    def _svc(self, name: str) -> str:
        return str(self.opt("server_url", "")).rstrip("/") + "/" + name

    def _prepare_session(self, s) -> None:
        body = {"acctID": str(self.opt("acct_id", "")), "username": str(self.opt("username", "")),
                "password": self.secret("password"), "lcid": self.opt_int("lcid", 2052)}
        resp = s.post(self._svc(str(self.opt("login_service", LOGIN_SERVICE))), json=body,
                      timeout=self.opt_int("timeout_sec", 60))
        if resp.status_code >= 400:
            raise ConnectorError(f"Kingdee: вход — HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorError("Kingdee: ответ на вход не JSON (проверьте server_url)") from exc
        if not (data.get("LoginResultType") == 1 or data.get("IsSuccessByAPI") is True):
            raise ConnectorError(f"Kingdee: вход не выполнен: "
                                 f"{data.get('Message') or data.get('LoginResultType')}")

    def _check_payload(self, data) -> None:
        node = data
        while isinstance(node, list) and node and isinstance(node[0], list):
            node = node[0]
        if isinstance(node, list) and node and isinstance(node[0], dict):
            node = node[0]
        if isinstance(node, dict) and "Result" in node:
            st = (node.get("Result") or {}).get("ResponseStatus") or {}
            if st.get("IsSuccess") is False or st.get("Errors"):
                errs = "; ".join(str(e.get("Message")) for e in st.get("Errors") or [])
                raise ConnectorError(f"Kingdee: ошибка запроса: {errs or st}")

    def _columns(self) -> list[str]:
        return [c.strip() for c in str(self.opt("field_keys", "")).split(",") if c.strip()]

    def _fetch(self, s, first_page_only: bool = False) -> list:
        url = self._svc(str(self.opt("query_service", QUERY_SERVICE)))
        size = self.opt_int("page_size", 2000)
        start = 0
        records: list = []
        for _ in range(1 if first_page_only else self.opt_int("max_pages", 1000)):
            body = {"data": {"FormId": str(self.opt("form_id", "")),
                             "FieldKeys": ",".join(self._columns()),
                             "FilterString": str(self.opt("filter_string", "")),
                             "OrderString": str(self.opt("order_string", "")),
                             "TopRowCount": 0, "StartRow": start, "Limit": size}}
            data = self._request(s, "POST", url, None, body)
            batch = data if isinstance(data, list) else self._extract(data)
            records.extend(batch)
            if len(batch) < size:
                break
            start += size
        return records
