# -*- coding: utf-8 -*-
"""SibDoc3: закрытый контур — единая проверка исходящих сетевых обращений.

Каждое обращение программы к другому компьютеру перед соединением проходит через
check() этого модуля: курсы ЦБ РФ и CFETS (app/rates/providers.py), коннекторы
(REST, SAP OData, 1С, Kingdee, Yonyou, почта IMAP, SFTP, база данных — общий вход
в app/connectors/base.py), уведомления (WeCom, Telegram — app/notify/base.py,
почта SMTP — app/notify/smtp_email.py). Список всех обращений — docs/OFFLINE.md.

Когда закрытый контур включён (в SibDoc 3 — по умолчанию), разрешены только адреса
из списка offline.allow_hosts: сам компьютер, локальная сеть, серверы внутри
контура (1С, SAP, база данных). Обращение к любому другому адресу отклоняется
сразу, понятной ошибкой: без попытки соединения, без ожидания таймаута и без
запроса DNS (имя сравнивается со списком как есть).

Настройки — config/system.yaml:

  offline:
    enabled: true                      # закрытый контур включён
    allow_hosts:                       # что можно внутри контура
      - localhost
      - 127.0.0.0/8                    # подсеть (CIDR)
      - 10.0.0.0/8
      - 1c-server                      # имя сервера
      - "*.corp.local"                 # маска имени

Переменные окружения (важнее файла):
  SIBDOC_OFFLINE=1 | 0                  — включить / выключить закрытый контур;
  SIBDOC_OFFLINE_ALLOW="a, 10.1.0.0/16" — добавить адреса к allow_hosts.

Публичный API:
  is_enabled(cfg=None) -> bool
  blocked_reason(target, what="", cfg=None) -> str  («» — можно)
  check(target, what="", cfg=None)       — OfflineBlocked, если нельзя
  check_url / check_host                 — то же самое (для читаемости кода)
  guarded_requests(requests, error=None, what="") — обёртка модуля requests
  status(cfg=None) -> dict               — для интерфейса и /api/offline
"""
from __future__ import annotations

import fnmatch
import ipaddress
import os
from urllib.parse import parse_qs, urlsplit

# Список по умолчанию, если в config/system.yaml нет раздела offline:
# только сам компьютер — самый строгий вариант.
DEFAULT_ALLOW = ["localhost", "127.0.0.0/8", "::1"]
DEFAULT_ENABLED = True            # SibDoc 3: закрытый контур включён по умолчанию

LABEL_ON = "Закрытый контур: интернет не используется"
LABEL_OFF = "Есть доступ в интернет"

_TRUE = ("1", "true", "yes", "on", "да", "y")
_FALSE = ("0", "false", "no", "off", "нет", "n")


class OfflineBlocked(ConnectionError):
    """Обращение к адресу вне контура отклонено (закрытый контур)."""

    def __init__(self, message: str, host: str = ""):
        super().__init__(message)
        self.host = host


# --------------------------------------------------------------------- настройки
_MEMO: dict[str, tuple] = {}


def _load_cfg() -> dict:
    """Раздел offline из config/system.yaml; файл перечитывается, только если изменился
    (проверка вызывается на каждый курс в прогоне — без памяти это тысячи разборов YAML)."""
    try:
        from app import config_loader
        path = config_loader.DEFAULT_CONFIG_PATH
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        memo = _MEMO.get("cfg")
        if memo and memo[0] == key:
            return memo[1]
        cfg = {"offline": (config_loader.load_config(path) or {}).get("offline")}
        _MEMO["cfg"] = (key, cfg)
        return cfg
    except Exception:  # noqa: BLE001 — сломанный конфиг не должен открывать доступ наружу
        return {}


def _to_bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v if v is not None else "").strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return default


def _split(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        items = list(v)
    else:
        items = str(v).replace(";", ",").replace("\n", ",").split(",")
    return [str(x).strip() for x in items if str(x).strip()]


def settings(cfg: dict | None = None) -> dict:
    """{"enabled", "allow_hosts", "source"} с учётом переменных окружения."""
    if cfg is None:
        cfg = _load_cfg()
    sec = (cfg or {}).get("offline")
    sec = sec if isinstance(sec, dict) else {}
    enabled = _to_bool(sec.get("enabled"), DEFAULT_ENABLED)
    source = "config" if "enabled" in sec else "default"
    env = os.environ.get("SIBDOC_OFFLINE", "").strip().lower()
    if env in _TRUE or env in _FALSE:
        enabled = env in _TRUE
        source = "env"
    allow = _split(sec.get("allow_hosts")) if "allow_hosts" in sec else list(DEFAULT_ALLOW)
    allow += [a for a in _split(os.environ.get("SIBDOC_OFFLINE_ALLOW")) if a not in allow]
    return {"enabled": enabled, "allow_hosts": allow, "source": source}


def is_enabled(cfg: dict | None = None) -> bool:
    return settings(cfg)["enabled"]


# --------------------------------------------------------------------- адреса
def host_of(target) -> str:
    """Имя или IP сервера из адреса: URL, «host:port», «[::1]:8080», строка БД SQLAlchemy.

    Пустая строка — адреса нет (локальный файл, sqlite:///… и т. п.).
    """
    s = str(target or "").strip()
    if not s:
        return ""
    bare = _as_ip(s.strip("[]"))
    if bare is not None:                   # «::1», «10.0.0.5» без порта
        return str(bare)
    if "://" not in s:
        s = "//" + s                       # «host:port» -> разобрать как адрес без схемы
    try:
        parts = urlsplit(s)
        host = parts.hostname or ""
    except ValueError:
        return ""
    return host.strip().rstrip(".").lower()


def _query_hosts(target) -> list[str]:
    """Сервер, переданный параметром строки подключения (postgresql:///db?host=…)."""
    s = str(target or "")
    if "?" not in s:
        return []
    try:
        q = parse_qs(urlsplit(s if "://" in s else "//" + s).query)
    except ValueError:
        return []
    out = []
    for key in ("host", "server", "hostaddr"):
        for v in q.get(key, []):
            for item in v.split(","):
                item = item.strip()
                if item and not item.startswith("/"):        # /var/run/… — локальный сокет
                    out.append(host_of(item))
    return [h for h in out if h]


def _as_ip(host: str):
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


def host_allowed(host: str, allow: list[str]) -> bool:
    """Адрес в списке: точное имя, маска имени (*.corp.local) или подсеть (10.0.0.0/8)."""
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return True
    ip = _as_ip(host)
    for raw in allow or []:
        item = str(raw).strip().rstrip(".").lower()
        if not item:
            continue
        if ip is not None:
            try:
                if ip in ipaddress.ip_network(item, strict=False):
                    return True
                continue
            except ValueError:
                pass
            if item == host:
                return True
            continue
        if any(ch in item for ch in "*?["):
            if fnmatch.fnmatchcase(host, item):
                return True
        elif item == host:
            return True
    return False


def blocked_reason(target, what: str = "", cfg: dict | None = None) -> str:
    """Текст отказа, если обращение к target нельзя выполнить; «» — можно."""
    st = settings(cfg)
    if not st["enabled"]:
        return ""
    hosts = [host_of(target)] + _query_hosts(target)
    for host in hosts:
        if host and not host_allowed(host, st["allow_hosts"]):
            who = f" ({what})" if what else ""
            return (f"Закрытый контур: обращение к {host}{who} заблокировано — адреса нет в списке "
                    f"разрешённых (config/system.yaml → offline.allow_hosts). "
                    f"Интернет в закрытом контуре не используется.")
    return ""


def check(target, what: str = "", cfg: dict | None = None) -> None:
    """Проверить обращение; нельзя — OfflineBlocked с понятным текстом (по-русски)."""
    msg = blocked_reason(target, what, cfg)
    if msg:
        raise OfflineBlocked(msg, host_of(target))


check_url = check
check_host = check


def status(cfg: dict | None = None) -> dict:
    """Состояние для интерфейса: подпись, список разрешённых адресов, откуда настройка."""
    st = settings(cfg)
    return {"offline": st["enabled"], "label": LABEL_ON if st["enabled"] else LABEL_OFF,
            "allow_hosts": st["allow_hosts"], "source": st["source"]}


# --------------------------------------------------------------------- requests
def guarded_requests(requests_mod, error: type[Exception] | None = None, what: str = ""):
    """Модуль requests, у которого каждый запрос (и каждый переход по редиректу)
    сначала проходит check(). error — класс исключения вызывающего кода
    (ConnectorError, NotifyError), чтобы текст отказа показывался без префиксов."""
    return _GuardedRequests(requests_mod, error, what)


class _GuardedRequests:
    def __init__(self, requests_mod, error, what):
        self._r = requests_mod
        self._error = error
        self._what = what
        guard = self

        class Session(requests_mod.Session):
            def request(self, method, url, *a, **kw):        # до отправки (и до подмен в тестах)
                guard._check(url)
                return super().request(method, url, *a, **kw)

            def send(self, request, **kw):                    # каждый запрос, включая редиректы
                guard._check(getattr(request, "url", ""))
                return super().send(request, **kw)

        self.Session = Session
        self.session = Session

    def _check(self, url) -> None:
        try:
            check(url, self._what)
        except OfflineBlocked as exc:
            if self._error is None:
                raise
            raise self._error(str(exc)) from None

    def request(self, method, url, **kw):
        self._check(url)
        with self.Session() as s:
            return s.request(method=method, url=url, **kw)

    def get(self, url, params=None, **kw):
        return self.request("get", url, params=params, **kw)

    def post(self, url, data=None, json=None, **kw):
        return self.request("post", url, data=data, json=json, **kw)

    def put(self, url, data=None, **kw):
        return self.request("put", url, data=data, **kw)

    def patch(self, url, data=None, **kw):
        return self.request("patch", url, data=data, **kw)

    def delete(self, url, **kw):
        return self.request("delete", url, **kw)

    def head(self, url, **kw):
        kw.setdefault("allow_redirects", False)
        return self.request("head", url, **kw)

    def __getattr__(self, name):                              # исключения, структуры и т. п.
        return getattr(self._r, name)
