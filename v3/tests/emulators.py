# -*- coding: utf-8 -*-
"""Эмуляторы внешних систем для тестов коннекторов (Коннекторы/банк): настоящий HTTP на
127.0.0.1 (stdlib http.server), протоколы — по опубликованным описаниям API:

  * Kingdee K3Cloud WebAPI: ValidateUser / LoginByAppSecret (сессия — cookie kdservice-sessionid),
    ExecuteBillQuery (StartRow / Limit, ответ — массив строк), ошибки ResponseStatus (MsgCode 1 —
    сессия потеряна);
  * Yonyou YonBIP OpenAPI: getGatewayAddress, selfAppAuth getAccessToken (HmacSHA256 + Base64),
    API со страницами pageIndex/pageSize/pageCount, истечение токена;
  * 1С OData (standard.odata): список объектов, $top/$skip/$filter/$select, basic-авторизация,
    ошибки {"odata.error": …}.

Это эмуляторы, а не копии систем: что проверено здесь, проверено на эмуляторе; на реальной
системе — после выдачи доступа. Запуск вручную (для показа в интерфейсе):
    python3 tests/emulators.py kingdee 8765
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


class Request:
    def __init__(self, method, path, query, headers, body):
        self.method, self.path, self.query, self.headers, self.body = method, path, query, headers, body

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8") or "null")
        except ValueError:
            return None

    def q(self, key, default=None):
        v = self.query.get(key)
        return v[0] if v else default

    def cookie(self, name):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[name].value if name in c else None


class MockServer:
    """Сервер на свободном порту; app.handle(Request) -> (status, headers, body: dict|list|bytes|str)."""

    def __init__(self, app, port: int = 0):
        self.app = app
        self.calls: list[Request] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # тише в выводе тестов
                pass

            def _do(self, method):
                u = urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                req = Request(method, unquote(u.path), parse_qs(u.query, keep_blank_values=True),
                              {k: v for k, v in self.headers.items()}, body)
                outer.calls.append(req)
                status, headers, payload = outer.app.handle(req)
                if isinstance(payload, (dict, list)):
                    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
                elif isinstance(payload, str):
                    data = payload.encode("utf-8")
                else:
                    data = payload or b""
                self.send_response(status)
                for k, v in (headers or {}).items():
                    for vv in (v if isinstance(v, list) else [v]):
                        self.send_header(k, vv)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


# ============================================================================ Kingdee K3Cloud
class KingdeeEmulator:
    SVC = "/k3cloud/Kingdee.BOS.WebApi.ServicesStub."

    def __init__(self, rows: list[dict], acct="DEMO-ACCT", user="api", password="pw", app_id="APP1",
                 app_secret="secret", expire_after: int | None = None):
        self.rows, self.acct, self.user, self.password = rows, acct, user, password
        self.app_id, self.app_secret = app_id, app_secret
        self.expire_after = expire_after
        self.sessions: dict[str, int] = {}
        self.logins = 0

    @staticmethod
    def _err(msg, code=0):
        return [[{"Result": {"ResponseStatus": {"ErrorCode": 500, "IsSuccess": False, "MsgCode": code,
                                                "Errors": [{"FieldName": None, "Message": msg, "DIndex": 0}],
                                                "SuccessEntitys": [], "SuccessMessages": []}}}]]

    def _login_ok(self):
        sid = secrets.token_hex(8)
        self.sessions[sid] = 0
        self.logins += 1
        return 200, {"Set-Cookie": [f"kdservice-sessionid={sid}; Path=/", "ASP.NET_SessionId=x; Path=/"]}, {
            "Message": None, "MessageCode": "CheckPasswordPolicy", "LoginResultType": 1,
            "Context": {"UserName": self.user, "UserToken": secrets.token_hex(8)},
            "KDSVCSessionId": sid, "IsSuccessByAPI": True}

    def handle(self, req: Request):
        body = req.json() or {}
        if isinstance(body, dict) and "parameters" in body:          # старый клиент ApiClient
            params = body["parameters"]
        else:
            params = None
        if req.path.endswith("AuthService.ValidateUser.common.kdsvc"):
            b = body if params is None else dict(zip(["acctID", "username", "password", "lcid"], params))
            if b.get("acctID") == self.acct and b.get("username") == self.user and b.get("password") == self.password:
                return self._login_ok()
            return 200, {}, {"Message": "用户名或密码错误！", "LoginResultType": 0, "IsSuccessByAPI": False}
        if req.path.endswith("AuthService.LoginByAppSecret.common.kdsvc"):
            if body.get("acctID") == self.acct and body.get("appId") == self.app_id and \
                    body.get("appSecret") == self.app_secret:
                return self._login_ok()
            return 200, {}, {"Message": "第三方系统登录授权验证失败", "LoginResultType": -1, "IsSuccessByAPI": False}
        if req.path.endswith("DynamicFormService.ExecuteBillQuery.common.kdsvc"):
            sid = req.cookie("kdservice-sessionid") or req.headers.get("kdservice-sessionid")
            if sid not in self.sessions:
                return 200, {}, self._err("会话信息已丢失，请重新登录", 1)
            self.sessions[sid] += 1
            if self.expire_after is not None and self.sessions[sid] > self.expire_after:
                del self.sessions[sid]
                return 200, {}, self._err("会话信息已丢失，请重新登录", 1)
            data = json.loads(params[0]) if params else (body.get("data") or {})
            keys = [k.strip() for k in str(data.get("FieldKeys", "")).split(",") if k.strip()]
            bad = [k for k in keys if self.rows and k not in self.rows[0]]
            if bad:
                return 200, {}, self._err(f"字段{bad[0]}不存在", 9)
            start, limit = int(data.get("StartRow", 0)), int(data.get("Limit", 0)) or len(self.rows)
            return 200, {}, [[r[k] for k in keys] for r in self.rows[start:start + limit]]
        return 404, {}, "not found"


# ============================================================================ Yonyou YonBIP
def yy_sign(app_key, app_secret, ts):
    msg = f"appKey{app_key}timestamp{ts}".encode()
    return base64.b64encode(hmac.new(app_secret.encode(), msg, hashlib.sha256).digest()).decode()


class YonyouEmulator:
    def __init__(self, records: list[dict], app_key="AK", app_secret="AS", tenant_id="T1", token_ttl=7200,
                 expired_style="payload", page_meta=True):
        self.records, self.app_key, self.app_secret, self.tenant = records, app_key, app_secret, tenant_id
        self.token_ttl, self.expired_style, self.page_meta = token_ttl, expired_style, page_meta
        self.tokens: dict[str, float] = {}
        self.base = ""
        self.revoke_next = False

    def handle(self, req: Request):
        if req.path.endswith("/open-auth/dataCenter/getGatewayAddress"):
            if req.q("tenantId") != self.tenant:
                return 200, {}, {"code": "999", "message": "租户不存在"}
            return 200, {}, {"code": "00000", "message": "成功！",
                             "data": {"gatewayUrl": self.base + "/iuap-api-gateway",
                                      "tokenUrl": self.base + "/iuap-api-auth"}}
        if req.path.endswith("/open-auth/selfAppAuth/getAccessToken"):
            ts, sig = req.q("timestamp", ""), req.q("signature", "")
            if req.q("appKey") != self.app_key or sig != yy_sign(self.app_key, self.app_secret, ts):
                return 200, {}, {"code": "10000", "message": "签名不正确"}
            tok = "YT-" + secrets.token_hex(6)
            self.tokens[tok] = time.time() + self.token_ttl
            return 200, {}, {"code": "00000", "message": "成功！", "data": {"access_token": tok, "expire": self.token_ttl}}
        if req.path.startswith("/iuap-api-gateway/"):
            tok = req.q("access_token")
            if self.revoke_next and tok in self.tokens:
                self.revoke_next = False
                del self.tokens[tok]
            if tok not in self.tokens or self.tokens[tok] < time.time():
                if self.expired_style == "http401":
                    return 401, {}, {"code": "401", "message": "Unauthorized"}
                return 200, {}, {"code": "1090003500065", "message": "access_token 已失效，请重新获取"}
            if not req.path.endswith("/yonbip/scm/purchaseorder/list"):
                return 200, {}, {"code": "999", "message": "API不存在"}
            body = req.json() or {}
            if body.get("bad"):
                return 200, {}, {"code": "999", "message": "参数错误：bad"}
            page, size = int(body.get("pageIndex", 1)), int(body.get("pageSize", 10))
            chunk = self.records[(page - 1) * size: page * size]
            data = {"pageIndex": page, "pageSize": size, "recordList": chunk}
            if self.page_meta:
                data.update(recordCount=len(self.records), pageCount=-(-len(self.records) // size))
            return 200, {}, {"code": "200", "message": "操作成功", "data": data}
        return 404, {}, "not found"


# ============================================================================ 1С OData
class OneCODataEmulator:
    ROOT = "/trade/odata/standard.odata"

    def __init__(self, entities: dict[str, list[dict]], user="sibdoc", password="pw"):
        self.entities, self.user, self.password = entities, user, password

    def handle(self, req: Request):
        auth = req.headers.get("Authorization") or ""
        ok = auth.startswith("Basic ") and base64.b64decode(auth[6:]).decode("utf-8") == f"{self.user}:{self.password}"
        if not ok:
            return 401, {"WWW-Authenticate": 'Basic realm="1C"'}, ""
        if not req.path.startswith(self.ROOT):
            return 404, {}, "not found"
        name = req.path[len(self.ROOT):].strip("/")
        if not name:
            return 200, {}, {"odata.metadata": f"{self.ROOT}/$metadata",
                             "value": [{"name": n, "url": n} for n in self.entities]}
        if name not in self.entities:
            return 400, {}, {"odata.error": {"code": "-1", "message": {"lang": "ru", "value":
                                                                      f"Не найден ресурс: {name}"}}}
        rows = list(self.entities[name])
        flt = req.q("$filter")
        if flt:
            if flt.strip() != "Posted eq true":
                return 400, {}, {"odata.error": {"code": "-1", "message": {"lang": "ru",
                                                                          "value": f"Ошибка в выражении: {flt}"}}}
            rows = [r for r in rows if r.get("Posted") is True]
        skip, top = int(req.q("$skip", 0)), req.q("$top")
        rows = rows[skip:skip + int(top)] if top else rows[skip:]
        sel = req.q("$select")
        if sel:
            cols = [c.strip() for c in sel.split(",")]
            rows = [{c: r.get(c) for c in cols} for r in rows]
        return 200, {}, {"odata.metadata": f"{self.ROOT}/$metadata#{name}", "value": rows}


def main(argv):
    kind, port = (argv + ["kingdee", "8765"])[:2]
    app = {"kingdee": lambda: KingdeeEmulator([{"FBillNo": f"CGDD{i:06d}", "FDate": "2026-10-01", "FQty": i}
                                               for i in range(1, 6)]),
           "yonyou": lambda: YonyouEmulator([{"code": f"PO{i}"} for i in range(1, 6)]),
           "onec": lambda: OneCODataEmulator({"Document_ПоступлениеТоваровУслуг": [{"Number": "1", "Posted": True}]})}[kind]()
    srv = MockServer(app, int(port))
    if isinstance(app, YonyouEmulator):
        app.base = srv.url
    print(f"{kind}: {srv.url}  (Ctrl+C — стоп)")
    srv.httpd.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
