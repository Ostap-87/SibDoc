# -*- coding: utf-8 -*-
"""Yonyou YonBIP / YonSuite (用友) OpenAPI (Коннекторы/банк: обмен реализован, проверен на эмуляторе).

1) Адреса дата-центра арендатора (если известен только tenant_id):
     GET <discovery_url>?tenantId=<id>   (по умолчанию
     https://apigateway.yonyoucloud.com/open-auth/dataCenter/getGatewayAddress)
     ответ {"code": "00000", "data": {"gatewayUrl": "...", "tokenUrl": "..."}};
2) Токен самостоятельного приложения (自建应用):
     GET <auth_url>/open-auth/selfAppAuth/getAccessToken?appKey=..&timestamp=<мс>&signature=..
     (в новых версиях путь /open-auth/selfAppAuth/base/v1/getAccessToken — параметр token_path)
     signature = URLEncode(Base64(HmacSHA256(AppSecret, "appKey" + AppKey + "timestamp" + timestamp)))
     ответ {"code": "00000", "data": {"access_token": "...", "expire": 7200}} — expire в секундах;
3) Запрос API: <gateway_url>/<api_path>?access_token=..., обычно POST с телом
     {"pageIndex": 1, "pageSize": 100, ...}; ответ {"code": "200", "data": {"recordList": [...],
     "pageCount": N, "recordCount": M}}; ошибка — {"code": "<не 200>", "message": "..."}.
Токен запрашивается заново, когда истёк срок (expire, с запасом) и когда API ответил, что токен
недействителен (HTTP 401 или код/текст из token_error_codes / «token … 过期/失效/invalid/expired»), —
страница повторяется один раз. Путь API и тело — по документу (закупка, приход и т.п.) у владельца
системы. Параметры token_path, api_path, body, records_path переопределяются.

  connectors:
    yonyou:
      type: yonyou
      enabled: false
      label: Yonyou 用友 YonBIP
      auth_url: "https://<dc>.yonyoucloud.com/iuap-api-auth"     # или tenant_id
      gateway_url: "https://<dc>.yonyoucloud.com/iuap-api-gateway"
      app_key: ""
      app_secret_env: SIBDOC_YONYOU_APP_SECRET
      api_path: "/yonbip/scm/purchaseorder/list"                  # уточнить у владельца системы
      body: {}                         # фильтры запроса
      records_path: data.recordList
      slot: purchase
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time

from .base import ConnectorError, P, STAGE_NEEDS_ACCESS, TLS_PARAMS
from .rest_api import RestConnector, get_path

TOKEN_PATH = "/open-auth/selfAppAuth/getAccessToken"
DISCOVERY_URL = "https://apigateway.yonyoucloud.com/open-auth/dataCenter/getGatewayAddress"
OK_CODES = ("200", "00000", "0")
TOKEN_WORDS = re.compile(r"(?i)token.*(过期|失效|无效|invalid|expired)|(过期|失效|无效).*token")


def yonyou_signature(app_key: str, app_secret: str, timestamp: str) -> str:
    msg = f"appKey{app_key}timestamp{timestamp}".encode("utf-8")
    digest = hmac.new(app_secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class _TokenExpired(ConnectorError):
    pass


class YonyouConnector(RestConnector):
    type = "yonyou"
    title = "Yonyou YonBIP / YonSuite (用友) OpenAPI"
    stage = STAGE_NEEDS_ACCESS
    verified = "emulator"
    access_hint = ("у администратора YonBIP (用友): создать самостоятельное приложение (自建应用) в "
                   "Open Platform (开放平台), выдать AppKey/AppSecret с правом на нужный API, "
                   "сообщить адреса шлюза дата-центра (auth_url, gateway_url) или tenant_id и путь API документа")
    need = ("самостоятельное приложение (自建应用): AppKey и AppSecret с правом на API документа, адреса шлюза "
            "дата-центра (или tenant_id), путь API (закупка, приход …)")
    need_from = "у администратора YonBIP 用友 китайской стороны (开放平台 → 自建应用)"
    params = [
        P("auth_url", "Адрес сервиса токенов", required=True,
          example="https://<dc>.yonyoucloud.com/iuap-api-auth"),
        P("gateway_url", "Адрес API-шлюза", required=True,
          example="https://<dc>.yonyoucloud.com/iuap-api-gateway"),
        P("tenant_id", "ID арендатора (вместо адресов шлюза)", example="tenant id из 开放平台"),
        P("app_key", "AppKey", required=True),
        P("app_secret", "AppSecret", required=True, secret=True,
          example="app_secret_env: SIBDOC_YONYOU_APP_SECRET"),
        P("api_path", "Путь API (уточнить у владельца системы)", required=True,
          example="/yonbip/scm/purchaseorder/list"),
        P("method", "Метод", example="POST"),
        P("body", "Тело запроса (фильтры)", example='{"isSum": false}'),
        P("records_path", "Путь к записям", example="data.recordList"),
        P("page_size", "Размер страницы", example="100"),
        P("slot", "Слот (файл <slot>.csv)", required=True, example="purchase"),
    ] + TLS_PARAMS
    defaults = {"auth": "none", "method": "POST", "records_path": "data.recordList",
                "pagination": "page", "page_param": "pageIndex", "page_size_param": "pageSize",
                "page_size": 100, "page_in": "body", "body": {}, "token_error_codes": ["401"]}

    def _param_set(self, p: dict) -> bool:
        # адреса шлюза не нужны, если задан tenant_id (адреса узнаются у сервиса дата-центров)
        if p["key"] in ("auth_url", "gateway_url") and self.opt("tenant_id"):
            return True
        return super()._param_set(p)

    def _offline_targets(self) -> list[str]:
        # SibDoc3: закрытый контур — при tenant_id без адресов шлюза первым идёт запрос к сервису
        # дата-центров (по умолчанию — облако Yonyou), его адрес тоже проверяется до соединения;
        # адреса из ответа сервиса проверяет каждый запрос сессии (netguard.guarded_requests)
        out = super()._offline_targets()
        if (not self.opt("auth_url") or not self.opt("gateway_url")) and self.opt("tenant_id") \
                and not self.opt("discovery_url"):
            out.append(DISCOVERY_URL)
        return out

    # ---------------------------------------------------------------- адреса и токен
    def _discover(self, s) -> None:
        if getattr(self, "_urls", None):
            return
        auth, gw = self.opt("auth_url"), self.opt("gateway_url")
        if (not auth or not gw) and self.opt("tenant_id"):
            resp = s.get(str(self.opt("discovery_url", DISCOVERY_URL)), params={"tenantId": str(self.opt("tenant_id"))},
                         timeout=self.opt_int("timeout_sec", 60))
            try:
                data = resp.json()
            except ValueError as exc:
                raise ConnectorError("Yonyou: ответ сервиса дата-центров не JSON") from exc
            d = (data or {}).get("data") or {}
            if str((data or {}).get("code")) != "00000" or not d.get("gatewayUrl"):
                raise ConnectorError(f"Yonyou: адрес дата-центра не получен: {(data or {}).get('message')}")
            auth, gw = auth or d.get("tokenUrl"), gw or d.get("gatewayUrl")
        self._urls = (str(auth or "").rstrip("/"), str(gw or "").rstrip("/"))

    def _token(self, s) -> None:
        ts = str(int(time.time() * 1000))
        key = str(self.opt("app_key", ""))
        url = self._urls[0] + str(self.opt("token_path", TOKEN_PATH))
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
        expire = (data.get("data") or {}).get("expire") or 7200
        try:
            expire = int(expire)
        except (TypeError, ValueError):
            expire = 7200
        self._auth_query = {"access_token": token}
        self._token_until = time.time() + max(60, expire - self.opt_int("token_margin_sec", 300))
        self._tokens = getattr(self, "_tokens", 0) + 1

    def _prepare_session(self, s) -> None:
        self._urls = None
        self._discover(s)
        self._token(s)

    def _base_url(self) -> str:
        return self._urls[1] + "/" + str(self.opt("api_path", "")).lstrip("/")

    # ---------------------------------------------------------------- запросы
    def _check_payload(self, data) -> None:
        if isinstance(data, dict) and "code" in data and str(data["code"]) not in OK_CODES:
            code, msg = str(data.get("code")), str(data.get("message") or "")
            if code in [str(c) for c in self.opt_list("token_error_codes")] or TOKEN_WORDS.search(msg):
                raise _TokenExpired(f"Yonyou: токен недействителен: {code} {msg}")
            raise ConnectorError(f"Yonyou: {code}: {msg}")

    def _request(self, s, method: str, url: str, params=None, body=None):
        if time.time() >= getattr(self, "_token_until", 0):
            self._token(s)
        try:
            return super()._request(s, method, url, params, body)
        except _TokenExpired:
            self._token(s)
        except ConnectorError as exc:
            if not str(exc).startswith("HTTP 401"):
                raise
            self._token(s)
        self.last_notes.append("Yonyou: токен обновлён по ответу API")
        return super()._request(s, method, url, params, body)

    def _fetch(self, s, first_page_only: bool = False) -> list:
        """Страницы pageIndex/pageSize; конец — по pageCount/recordCount, если API их отдаёт."""
        records: list = []
        size = self.opt_int("page_size", 100)
        page = self.opt_int("page_start", 1)
        import copy
        max_pages = 1 if first_page_only else self.opt_int("max_pages", 1000)
        for _ in range(max_pages):
            b = copy.deepcopy(self.opt("body") or {})
            self._put({}, b, "body", str(self.opt("page_param", "pageIndex")), page)
            self._put({}, b, "body", str(self.opt("page_size_param", "pageSize")), size)
            data = self._request(s, str(self.opt("method", "POST")).upper(), self._base_url(), dict(self.opt("params") or {}), b)
            batch = self._extract(data)
            records.extend(batch)
            page_count = get_path(data, "data.pageCount")
            total = get_path(data, "data.recordCount")
            if not batch or len(batch) < size:
                break
            if isinstance(page_count, int) and page >= page_count:
                break
            if isinstance(total, int) and len(records) >= total:
                break
            page += 1
        else:
            if not first_page_only:
                self.last_notes.append(f"достигнут предел max_pages={max_pages}")
        return records

    def _test(self) -> dict:
        s = self._session()
        recs = self._fetch(s, first_page_only=True)
        return {"ok": True, "detail": f"Yonyou: токен получен, API отвечает, записей на первой странице: "
                                      f"{len(recs)} (проверено на эмуляторе; на реальной системе — после "
                                      "выдачи доступа)"}
