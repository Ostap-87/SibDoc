# -*- coding: utf-8 -*-
"""Yonyou YonBIP / YonSuite (用友) OpenAPI (задел; реализовано на базе rest).

1) Токен самостоятельного приложения (自建应用):
     GET <auth_url>/open-auth/selfAppAuth/getAccessToken?appKey=..&timestamp=<мс>&signature=..
     signature = URLEncode(Base64(HmacSHA256(AppSecret, "appKey" + AppKey + "timestamp" + timestamp)))
     ответ {"code": "00000", "data": {"access_token": "...", "expire": 7200}}
2) Запрос API: <gateway_url>/<api_path>?access_token=..., обычно POST с телом
     {"pageIndex": 1, "pageSize": 100, ...}; записи — data.recordList.
Адреса шлюзов зависят от дата-центра арендатора (getGatewayAddress?tenantId=...),
путь токена в новых версиях может отличаться (/open-auth/selfAppAuth/base/v1/getAccessToken),
путь API и тело — по документу (закупка, приход и т.п.): всё это уточнить у владельца
системы. Параметры token_path, api_path, body, records_path переопределяются.

  connectors:
    yonyou:
      type: yonyou
      enabled: false
      label: Yonyou 用友 YonBIP
      auth_url: "https://<dc>.yonyoucloud.com/iuap-api-auth"
      gateway_url: "https://<dc>.yonyoucloud.com/iuap-api-gateway"
      app_key: ""
      app_secret_env: SIBDOC_YONYOU_APP_SECRET
      api_path: ""                     # уточнить у владельца системы
      body: {}                         # фильтры запроса
      records_path: data.recordList
      slot: purchase
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from .base import ConnectorError, P, STAGE_NEEDS_ACCESS, TLS_PARAMS
from .rest_api import RestConnector

TOKEN_PATH = "/open-auth/selfAppAuth/getAccessToken"


def yonyou_signature(app_key: str, app_secret: str, timestamp: str) -> str:
    msg = f"appKey{app_key}timestamp{timestamp}".encode("utf-8")
    digest = hmac.new(app_secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class YonyouConnector(RestConnector):
    type = "yonyou"
    title = "Yonyou YonBIP / YonSuite (用友) OpenAPI"
    stage = STAGE_NEEDS_ACCESS
    access_hint = ("у администратора YonBIP (用友): создать самостоятельное приложение (自建应用) в "
                   "Open Platform (开放平台), выдать AppKey/AppSecret с правом на нужный API, "
                   "сообщить адреса шлюза дата-центра (auth_url, gateway_url) и путь API документа")
    params = [
        P("auth_url", "Адрес сервиса токенов", required=True,
          example="https://<dc>.yonyoucloud.com/iuap-api-auth"),
        P("gateway_url", "Адрес API-шлюза", required=True,
          example="https://<dc>.yonyoucloud.com/iuap-api-gateway"),
        P("app_key", "AppKey", required=True),
        P("app_secret", "AppSecret", required=True, secret=True,
          example="app_secret_env: SIBDOC_YONYOU_APP_SECRET"),
        P("api_path", "Путь API (уточнить у владельца системы)", required=True,
          example="/yonbip/..."),
        P("method", "Метод", example="POST"),
        P("body", "Тело запроса (фильтры)", example='{"isSum": false}'),
        P("records_path", "Путь к записям", example="data.recordList"),
        P("page_size", "Размер страницы", example="100"),
        P("slot", "Слот (файл <slot>.csv)", required=True, example="purchase"),
    ] + TLS_PARAMS
    defaults = {"auth": "none", "method": "POST", "records_path": "data.recordList",
                "pagination": "page", "page_param": "pageIndex", "page_size_param": "pageSize",
                "page_size": 100, "page_in": "body", "body": {}}

    def _prepare_session(self, s) -> None:
        ts = str(int(time.time() * 1000))
        key = str(self.opt("app_key", ""))
        url = str(self.opt("auth_url", "")).rstrip("/") + str(self.opt("token_path", TOKEN_PATH))
        resp = s.get(url, params={"appKey": key, "timestamp": ts,
                                  "signature": yonyou_signature(key, self.secret("app_secret"), ts)},
                     timeout=self.opt_int("timeout_sec", 60))
        if resp.status_code >= 400:
            raise ConnectorError(f"Yonyou: токен — HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorError("Yonyou: ответ сервиса токенов не JSON (проверьте auth_url)") from exc
        token = ((data or {}).get("data") or {}).get("access_token")
        if str(data.get("code")) != "00000" or not token:
            raise ConnectorError(f"Yonyou: токен не получен: {data.get('code')} {data.get('message')}")
        self._auth_query = {"access_token": token}

    def _base_url(self) -> str:
        return str(self.opt("gateway_url", "")).rstrip("/") + "/" + \
            str(self.opt("api_path", "")).lstrip("/")

    def _check_payload(self, data) -> None:
        if isinstance(data, dict) and "code" in data and str(data["code"]) not in ("200", "00000", "0"):
            raise ConnectorError(f"Yonyou: {data.get('code')}: {data.get('message')}")
