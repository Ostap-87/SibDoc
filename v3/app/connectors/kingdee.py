# -*- coding: utf-8 -*-
"""Kingdee K3Cloud / 金蝶云星空 WebAPI (Коннекторы/банк: обмен реализован, проверен на эмуляторе).

Эндпоинты (POST, JSON; сессия — cookie kdservice-sessionid после входа):
  вход по паролю:  <server_url>/Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc
           тело {"acctID": "<ID набора данных 账套>", "username": "...", "password": "...", "lcid": 2052};
  вход приложения (第三方系统登录授权, auth_mode: app_secret):
           <server_url>/Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc
           тело {"acctID", "username", "appId", "appSecret", "lcid"};
           успех — LoginResultType = 1 (или IsSuccessByAPI = true), в ответе KDSVCSessionId;
  запрос:  <server_url>/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc
           тело {"data": {"FormId": "PUR_PurchaseOrder", "FieldKeys": "FBillNo,FDate,...",
                          "FilterString": "", "OrderString": "", "TopRowCount": 0,
                          "StartRow": 0, "Limit": 2000}}
           (старый клиент: {"parameters": [<data JSON-строкой>]} — body_style: parameters);
           ответ — массив строк-массивов в порядке FieldKeys; страницы — StartRow/Limit, пока строк
           на странице не меньше Limit;
  ошибка — [[{"Result": {"ResponseStatus": {"IsSuccess": false, "MsgCode": N, "Errors": [{"Message": …}]}}}]]
           MsgCode 1 — контекст/сессия потеряны (会话信息已丢失): коннектор входит заново и повторяет
           страницу один раз.
Вход по подписи (X-Api-Signature, SDK 8.x) не реализован — уточнить у владельца системы, если
пароль/AppSecret не выдают. Пути сервисов переопределяются параметрами login_service / query_service.

  connectors:
    kingdee:
      type: kingdee
      enabled: false
      label: Kingdee 金蝶云星空
      server_url: "https://erp.company.cn/k3cloud"
      acct_id: ""                      # ID набора данных (账套ID)
      username: ""
      auth_mode: password              # password | app_secret
      password_env: SIBDOC_KINGDEE_PASSWORD
      # app_id: ""  /  app_secret_env: SIBDOC_KINGDEE_APP_SECRET   (auth_mode: app_secret)
      lcid: 2052                       # 2052 — китайский (упрощённый)
      form_id: PUR_PurchaseOrder
      field_keys: "FBillNo,FDate,FSupplierId.FName,FMaterialId.FNumber,FQty,FPrice"
      filter_string: "FDocumentStatus='C'"
      page_size: 2000
      slot: purchase
"""
from __future__ import annotations

import json

from .base import ConnectorError, P, STAGE_NEEDS_ACCESS, TLS_PARAMS
from .rest_api import RestConnector

SVC = "Kingdee.BOS.WebApi.ServicesStub."
LOGIN_SERVICE = SVC + "AuthService.ValidateUser.common.kdsvc"
APP_LOGIN_SERVICE = SVC + "AuthService.LoginByAppSecret.common.kdsvc"
QUERY_SERVICE = SVC + "DynamicFormService.ExecuteBillQuery.common.kdsvc"

# MsgCode в ResponseStatus (по описанию WebAPI K3Cloud; точные тексты — уточнить на реальной системе)
MSG_CODES = {1: "сессия истекла или потеряна (会话信息已丢失)", 2: "нет прав на объект или поле",
             3: "не указан объект (FormId)", 4: "исключение на сервере", 8: "ошибка в параметрах запроса",
             9: "поле или значение не существует", 14: "превышен лимит вызовов WebAPI"}
SESSION_LOST_WORDS = ("会话", "重新登录", "上下文丢失", "session")


class _SessionLost(ConnectorError):
    pass


class KingdeeConnector(RestConnector):
    type = "kingdee"
    title = "Kingdee K3Cloud (金蝶云星空) WebAPI"
    stage = STAGE_NEEDS_ACCESS
    verified = "emulator"
    access_hint = ("у администратора Kingdee (金蝶云星空): адрес сервера K3Cloud, ID набора данных "
                   "(账套ID / acctID), пользователь с правом WebAPI на нужный объект (или AppID/AppSecret "
                   "интеграции 第三方系统登录授权), FormId и поля FieldKeys (например закупочный заказ "
                   "PUR_PurchaseOrder)")
    need = ("адрес K3Cloud, ID набора данных (账套ID), пользователь WebAPI с паролем или AppID/AppSecret, "
            "FormId и поля FieldKeys")
    need_from = "у администратора Kingdee 金蝶云星空 китайской стороны (поставщика/покупателя)"
    params = [
        P("server_url", "Адрес K3Cloud", required=True, example="https://erp.company.cn/k3cloud"),
        P("acct_id", "ID набора данных (账套ID)", required=True, example="5f2b8c..."),
        P("username", "Пользователь", required=True),
        P("auth_mode", "Вход: по паролю или приложением", example="password | app_secret"),
        P("password", "Пароль", secret=True, example="password_env: SIBDOC_KINGDEE_PASSWORD"),
        P("app_id", "AppID интеграции (для app_secret)"),
        P("app_secret", "AppSecret интеграции", secret=True, example="app_secret_env: SIBDOC_KINGDEE_APP_SECRET"),
        P("lcid", "Язык (2052 — китайский)", example="2052"),
        P("form_id", "FormId объекта (表单ID)", required=True, example="PUR_PurchaseOrder"),
        P("field_keys", "Поля (FieldKeys)", required=True, example="FBillNo,FDate,FQty,FPrice"),
        P("filter_string", "Фильтр (FilterString)", example="FDocumentStatus='C'"),
        P("page_size", "Строк за запрос (Limit)", example="2000"),
        P("slot", "Слот (файл <slot>.csv)", required=True, example="purchase"),
    ] + TLS_PARAMS
    defaults = {"auth": "none", "method": "POST", "page_size": 2000, "auth_mode": "password",
                "body_style": "data"}

    def _extra_missing(self) -> list[str]:
        if self._mode() == "app_secret":
            return [k for k, ok in (("app_id", bool(self.opt("app_id"))),
                                    ("app_secret", bool(self.secret("app_secret")))) if not ok]
        return [] if self.secret("password") else ["password"]

    def _mode(self) -> str:
        return str(self.opt("auth_mode", "password")).strip().lower()

    def _svc(self, name: str) -> str:
        return str(self.opt("server_url", "")).rstrip("/") + "/" + name

    # ---------------------------------------------------------------- вход
    def _prepare_session(self, s) -> None:
        lcid = self.opt_int("lcid", 2052)
        if self._mode() == "app_secret":
            svc = str(self.opt("login_service", APP_LOGIN_SERVICE))
            body = {"acctID": str(self.opt("acct_id", "")), "username": str(self.opt("username", "")),
                    "appId": str(self.opt("app_id", "")), "appSecret": self.secret("app_secret"), "lcid": lcid}
        else:
            svc = str(self.opt("login_service", LOGIN_SERVICE))
            body = {"acctID": str(self.opt("acct_id", "")), "username": str(self.opt("username", "")),
                    "password": self.secret("password"), "lcid": lcid}
        resp = s.post(self._svc(svc), json=body, timeout=self.opt_int("timeout_sec", 60))
        if resp.status_code >= 400:
            raise ConnectorError(f"Kingdee: вход — HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorError("Kingdee: ответ на вход не JSON (проверьте server_url)") from exc
        if not isinstance(data, dict) or not (data.get("LoginResultType") == 1 or data.get("IsSuccessByAPI") is True):
            msg = (data or {}).get("Message") if isinstance(data, dict) else data
            code = (data or {}).get("LoginResultType") if isinstance(data, dict) else ""
            raise ConnectorError(f"Kingdee: вход не выполнен ({code}): {msg or 'нет текста ошибки'}")
        sid = data.get("KDSVCSessionId")
        if sid:
            s.headers["kdservice-sessionid"] = str(sid)
            self._session_id = str(sid)
        self._logins = getattr(self, "_logins", 0) + 1

    def _secret_values(self) -> list[str]:
        vals = super()._secret_values()
        if getattr(self, "_session_id", ""):
            vals.append(self._session_id)
        return vals

    # ---------------------------------------------------------------- ответы
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
                code = st.get("MsgCode")
                if code == 1 or any(w in errs.lower() for w in SESSION_LOST_WORDS):
                    raise _SessionLost(f"Kingdee: {MSG_CODES[1]}: {errs}")
                hint = f" [{MSG_CODES[code]}]" if code in MSG_CODES else ""
                raise ConnectorError(f"Kingdee: ошибка запроса{hint}: {errs or st}")

    def _columns(self) -> list[str]:
        return [c.strip() for c in str(self.opt("field_keys", "")).split(",") if c.strip()]

    def _body(self, start: int, size: int) -> dict:
        data = {"FormId": str(self.opt("form_id", "")), "FieldKeys": ",".join(self._columns()),
                "FilterString": str(self.opt("filter_string", "")), "OrderString": str(self.opt("order_string", "")),
                "TopRowCount": self.opt_int("top_row_count", 0), "StartRow": start, "Limit": size}
        if str(self.opt("body_style", "data")).lower() == "parameters":
            return {"parameters": [json.dumps(data, ensure_ascii=False)]}
        return {"data": data}

    def _fetch(self, s, first_page_only: bool = False) -> list:
        url = self._svc(str(self.opt("query_service", QUERY_SERVICE)))
        size = self.opt_int("page_size", 2000)
        start = 0
        records: list = []
        max_pages = 1 if first_page_only else self.opt_int("max_pages", 1000)
        for _ in range(max_pages):
            try:
                data = self._request(s, "POST", url, None, self._body(start, size))
            except _SessionLost:
                # сессия истекла посреди выгрузки — войти заново и повторить эту страницу (один раз)
                self._prepare_session(s)
                self.last_notes.append("Kingdee: сессия истекла — выполнен повторный вход")
                data = self._request(s, "POST", url, None, self._body(start, size))
            batch = data if isinstance(data, list) else self._extract(data)
            records.extend(batch)
            if len(batch) < size or not batch:
                break
            start += size
        else:
            if not first_page_only:
                self.last_notes.append(f"достигнут предел max_pages={max_pages}")
        return records

    def _test(self) -> dict:
        s = self._session()
        recs = self._fetch(s, first_page_only=True)
        return {"ok": True, "detail": f"Kingdee: вход выполнен, {self.opt('form_id')}: строк на первой "
                                      f"странице {len(recs)} (проверено на эмуляторе; на реальной системе — "
                                      "после выдачи доступа)"}
