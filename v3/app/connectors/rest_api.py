# -*- coding: utf-8 -*-
"""Универсальный REST/JSON-коннектор — база для 1С OData, Kingdee, Yonyou и любых API.

Записи из ответа сохраняются в CSV (UTF-8 с BOM) в dest_dir под именем <slot>.csv —
сервер положит файл в слот по имени. Маппинг полей не делается: колонки — ключи записей
(вложенные объекты — JSON-строкой).

  connectors:
    broker_api:
      type: rest
      enabled: false
      label: API брокера
      url: "https://api.broker.example/v1/declarations"
      method: GET                       # GET | POST
      headers: {"Accept": "application/json"}
      params: {"status": "released"}    # query-параметры
      body: null                        # JSON-тело для POST
      auth: bearer                      # none | basic | bearer | api_key_header
      token_env: SIBDOC_BROKER_TOKEN    # для bearer
      # username: svc  /  password_env: SIBDOC_API_PASSWORD   (basic)
      # api_key_header: X-API-Key  /  api_key_env: SIBDOC_API_KEY  (api_key_header)
      records_path: "data.list"         # где записи: d.results | value | data.list | (пусто — авто)
      pagination: auto                  # auto | none | next_link | page | offset
      # next_link_path: "links.next"    # для next_link (авто: @odata.nextLink, d.__next, next ...)
      # page_param: page / page_size_param: page_size / page_size: 100 / page_start: 1
      # offset_param: offset / limit_param: limit
      # page_in: query                  # query | body (номер страницы в JSON-теле, путь через точку)
      slot: cn_dec                      # -> cn_dec.csv
"""
from __future__ import annotations

import copy
from urllib.parse import urljoin

from .base import Connector, ConnectorError, P, STAGE_READY, TLS_PARAMS

NEXT_LINK_CANDIDATES = ("@odata.nextLink", "odata.nextLink", "d.__next", "__next",
                        "next", "links.next", "paging.next", "next_page_url", "nextLink")
RECORD_CANDIDATES = ("d.results", "value", "d", "data.list", "data.records", "data.recordList",
                     "data.items", "data.rows", "data", "items", "records", "rows", "results",
                     "result", "list")


def get_path(data, path: str):
    """'data.list' / 'd.results' / 'items.0.rows' -> вложенное значение или None."""
    node = data
    for part in [p for p in str(path).split(".") if p != ""]:
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list) and part.lstrip("-").isdigit():
            idx = int(part)
            if -len(node) <= idx < len(node):
                node = node[idx]
            else:
                return None
        else:
            return None
    return node


def set_path(data: dict, path: str, value) -> dict:
    parts = [p for p in str(path).split(".") if p]
    node = data
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    return data


def _get_any(data, path: str):
    """get_path, но путь с точкой может быть и одним ключом ('@odata.nextLink')."""
    if isinstance(data, dict) and path in data:
        return data[path]
    return get_path(data, path)


class RestConnector(Connector):
    type = "rest"
    title = "REST/JSON API (универсальный)"
    stage = STAGE_READY
    params = [
        P("url", "Адрес запроса (URL)", required=True,
          example="https://api.broker.example/v1/declarations"),
        P("method", "Метод", example="GET | POST"),
        P("headers", "Заголовки", example='{"Accept": "application/json"}'),
        P("params", "Параметры запроса (query)", example='{"status": "released"}'),
        P("body", "Тело запроса (JSON, для POST)", example='{"filter": {"days": 7}}'),
        P("auth", "Авторизация", example="none | basic | bearer | api_key_header"),
        P("username", "Логин (basic)"),
        P("password", "Пароль (basic)", secret=True, example="password_env: SIBDOC_API_PASSWORD"),
        P("token", "Токен (bearer)", secret=True, example="token_env: SIBDOC_API_TOKEN"),
        P("api_key_header", "Заголовок для ключа", example="X-API-Key"),
        P("api_key", "Ключ API", secret=True, example="api_key_env: SIBDOC_API_KEY"),
        P("records_path", "Путь к записям в ответе", example="d.results | value | data.list"),
        P("pagination", "Пагинация", example="auto | none | next_link | page | offset"),
        P("next_link_path", "Где ссылка на следующую страницу", example="links.next"),
        P("page_param", "Параметр номера страницы", example="page"),
        P("page_size_param", "Параметр размера страницы", example="page_size"),
        P("page_size", "Размер страницы", example="100"),
        P("slot", "Слот (файл <slot>.csv)", required=True, example="purchase"),
    ] + TLS_PARAMS

    # параметры по умолчанию для наследников (1С, Kingdee, Yonyou)
    defaults: dict = {}

    def opt(self, key: str, default=None):
        v = super().opt(key, None)
        if v is None:
            v = self.defaults.get(key)
        return default if v is None or v == "" else v

    def _auth_mode(self) -> str:
        return str(self.opt("auth", "none")).strip().lower()

    def _secret_values(self) -> list[str]:
        vals = super()._secret_values()
        vals += [str(v) for v in (getattr(self, "_auth_query", None) or {}).values() if v]
        return vals

    def _extra_missing(self) -> list[str]:
        mode = self._auth_mode()
        need = {"basic": [("username", bool(self.opt("username"))),
                          ("password", bool(self.secret("password")))],
                "bearer": [("token", bool(self.secret("token")))],
                "api_key_header": [("api_key", bool(self.secret("api_key")))]}.get(mode, [])
        return [k for k, ok in need if not ok]

    # ---------------------------------------------------------------- http
    def _session(self):
        requests = self._requests()
        s = requests.Session()
        s.verify = self._verify()
        s.headers.update({"Accept": "application/json"})
        for k, v in (self.opt("headers") or {}).items():
            s.headers[str(k)] = str(v)
        mode = self._auth_mode()
        if mode == "basic":
            s.auth = (str(self.opt("username", "")), self.secret("password"))
        elif mode == "bearer":
            s.headers["Authorization"] = f"Bearer {self.secret('token')}"
        elif mode == "api_key_header":
            s.headers[str(self.opt("api_key_header", "X-API-Key"))] = self.secret("api_key")
        elif mode != "none":
            raise ConnectorError(f"неизвестный auth: {mode} (none | basic | bearer | api_key_header)")
        self._auth_query: dict = {}
        self._prepare_session(s)
        return s

    def _prepare_session(self, s) -> None:
        """Хук наследников: вход (Kingdee), получение токена (Yonyou)."""

    def _request(self, s, method: str, url: str, params=None, body=None):
        q = dict(params or {})
        q.update(getattr(self, "_auth_query", {}) or {})
        kw = {"params": q or None, "timeout": self.opt_int("timeout_sec", 120)}
        if body is not None and method != "GET":
            kw["json"] = body
        resp = s.request(method, url, **kw)
        if resp.status_code >= 400:
            raise ConnectorError(f"HTTP {resp.status_code}: {(resp.text or '')[:200]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ConnectorError(f"ответ не JSON (HTTP {resp.status_code}): "
                                 f"{(resp.text or '')[:120]}") from exc
        self._check_payload(data)
        return data

    def _check_payload(self, data) -> None:
        """Хук наследников: ошибка, которую API отдаёт с HTTP 200."""

    def _extract(self, data) -> list:
        path = self.opt("records_path")
        if path:
            node = _get_any(data, str(path))
        elif isinstance(data, list):
            node = data
        else:
            node = None
            for cand in RECORD_CANDIDATES:
                v = _get_any(data, cand)
                if isinstance(v, list):
                    node = v
                    break
            if node is None:
                node = [data]
        if node is None:
            return []
        if isinstance(node, dict):
            return [node]
        return list(node) if isinstance(node, list) else [node]

    def _next_link(self, data) -> str | None:
        paths = [self.opt("next_link_path")] if self.opt("next_link_path") else NEXT_LINK_CANDIDATES
        for p in paths:
            v = _get_any(data, str(p))
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    def _base_url(self) -> str:
        return str(self.opt("url", ""))

    def _fetch(self, s, first_page_only: bool = False) -> list:
        method = str(self.opt("method", "GET")).upper()
        url = self._base_url()
        base_params = dict(self.opt("params") or {})
        base_body = copy.deepcopy(self.opt("body"))
        mode = str(self.opt("pagination", "auto")).strip().lower()
        size = self.opt_int("page_size", 100)
        page = self.opt_int("page_start", 1)
        offset = self.opt_int("offset_start", 0)
        page_in = str(self.opt("page_in", "query")).lower()
        records: list = []
        seen_urls = set()
        max_pages = 1 if first_page_only else self.opt_int("max_pages", 1000)
        for n in range(max_pages):
            q = dict(base_params) if (n == 0 or mode != "next_link" and mode != "auto") else {}
            b = copy.deepcopy(base_body)
            if mode == "page":
                self._put(q, b, page_in, str(self.opt("page_param", "page")), page)
                if self.opt("page_size_param"):
                    self._put(q, b, page_in, str(self.opt("page_size_param")), size)
            elif mode == "offset":
                self._put(q, b, page_in, str(self.opt("offset_param", "offset")), offset)
                self._put(q, b, page_in, str(self.opt("limit_param", "limit")), size)
            seen_urls.add(url)
            data = self._request(s, method, url, q, b)
            batch = self._extract(data)
            records.extend(batch)
            if mode in ("next_link", "auto"):
                nxt = self._next_link(data)
                if not nxt:
                    break
                nxt_url = urljoin(url, nxt)
                if nxt_url in seen_urls:
                    break
                url = nxt_url
            elif mode in ("page", "offset"):
                if len(batch) < size or not batch:
                    break
                page += 1
                offset += size
            else:
                break
        else:
            if not first_page_only:
                self.last_notes.append(f"достигнут предел max_pages={max_pages}")
        return records

    @staticmethod
    def _put(q: dict, b, where: str, key: str, value) -> None:
        if where == "body" and isinstance(b, dict):
            set_path(b, key, value)
        else:
            q[key] = value

    def _slot(self) -> str:
        return str(self.opt("slot") or (self.opt_list("slots") or [self.id])[0])

    def _columns(self) -> list[str] | None:
        cols = self.opt("columns")
        if isinstance(cols, str):
            cols = [c.strip() for c in cols.split(",") if c.strip()]
        return cols or None

    # ---------------------------------------------------------------- api
    def _test(self) -> dict:
        s = self._session()
        recs = self._fetch(s, first_page_only=True)
        return {"ok": True, "detail": f"API отвечает, записей на первой странице: {len(recs)}"}

    def _pull(self, dest_dir: str) -> list[dict]:
        s = self._session()
        records = self._fetch(s)
        if not records:
            raise ConnectorError("API вернул 0 записей — файл не перезаписан")
        slot = self._slot()
        fname = f"{slot}.csv"
        path, n = self._write_records_csv(dest_dir, fname, records, self._columns())
        return [{"slot": slot, "file": fname, "path": path, "rows": n}]
