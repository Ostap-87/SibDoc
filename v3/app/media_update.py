# -*- coding: utf-8 -*-
"""SibDoc3: закрытый контур — «Обновление с носителя».

Справочники (HS-коды, элементы декларации CDE, контракты), правила проверок и курсы
валют обновляются без интернета: на компьютере с интернетом собирается архив .zip
(tools/make_update_package.py), переносится на флешке и загружается в SibDoc
(«Обновление с носителя» в интерфейсе, POST /api/offline/update).

Архив:
  SHA256SUMS          — обязательно: строки «<sha256>  <путь>» (формат sha256sum) для КАЖДОГО файла;
  SHA256SUMS.sig      — подпись Ed25519 файла SHA256SUMS (необязательно; обязательно, если
                        offline.update.require_signature: true);
  UPDATE.txt          — описание (необязательно);
  references/…        — справочники (csv, xlsx, xls, json, yaml);
  config/rules/*.yaml — правила проверок;
  config/fields.yaml, config/scan_fields.yaml, config/scan_mapping.yaml, config/xml_profiles.yaml;
  rates/…             — файлы курсов (ЦБ РФ XML, CFETS, шаблон) — загружаются в курсы, не копируются.
config/system.yaml архивом не меняется (в нём настройки закрытого контура и доступа).

Проверка до установки: каждый файл архива есть в SHA256SUMS и совпадает по SHA-256,
каждый файл из SHA256SUMS есть в архиве, пути только из списка выше, YAML читается,
подпись (если есть) — доверенным ключом из offline.update.keys_dir (*.pub).
Установка: прежние версии файлов — в storage/updates/<время>/backup/, затем замена;
если после замены не читается config/system.yaml или правила — всё возвращается назад.
Каждая установка (и отказ) — запись «media_update» в журнале.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import time
import zipfile

import yaml

from app.journal import log

PROJECT_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
UPDATES_DIR = os.path.join(PROJECT_ROOT, "storage", "updates")

SUMS = "SHA256SUMS"
SIG = "SHA256SUMS.sig"
INFO = ("UPDATE.txt", "README.txt", "MANIFEST.json")

ALLOWED_CONFIG = ("config/fields.yaml", "config/scan_fields.yaml", "config/scan_mapping.yaml",
                  "config/xml_profiles.yaml")
REF_EXTS = (".csv", ".xlsx", ".xls", ".json", ".yaml", ".yml", ".txt")
RATE_EXTS = (".xml", ".xlsx", ".xls", ".csv")
MAX_FILES = 2000
MAX_TOTAL = 500 * 2**20


class UpdateError(ValueError):
    """Архив не прошёл проверку (текст — по-русски)."""


# ===================================================================== настройки
def update_settings(cfg: dict | None = None) -> dict:
    if cfg is None:
        try:
            from app.config_loader import load_config
            cfg = load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    sec = ((cfg or {}).get("offline") or {}).get("update") or {}
    keys_dir = str(sec.get("keys_dir") or "config/update_keys")
    if not os.path.isabs(keys_dir):
        keys_dir = os.path.join(PROJECT_ROOT, keys_dir)
    req = sec.get("require_signature", False)
    if isinstance(req, str):
        req = req.strip().lower() in ("1", "true", "yes", "on", "да")
    return {"require_signature": bool(req), "keys_dir": keys_dir}


def trusted_keys(keys_dir: str) -> dict[str, bytes]:
    """Открытые ключи Ed25519: {имя файла: 32 байта}. Формат файла — PEM или base64 32 байт."""
    out: dict[str, bytes] = {}
    if not os.path.isdir(keys_dir):
        return out
    for name in sorted(os.listdir(keys_dir)):
        if not name.endswith(".pub"):
            continue
        try:
            raw = open(os.path.join(keys_dir, name), "rb").read()
            out[name] = _pub_bytes(raw)
        except (OSError, ValueError):
            continue
    return out


def _pub_bytes(raw: bytes) -> bytes:
    text = raw.decode("ascii", "ignore").strip()
    if "BEGIN PUBLIC KEY" in text:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = serialization.load_pem_public_key(raw)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("ключ не Ed25519")
        return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    data = base64.b64decode(text.split()[-1] if text else "", validate=False)
    if len(data) != 32:
        raise ValueError("ожидается 32 байта открытого ключа Ed25519 (base64)")
    return data


# ===================================================================== разбор архива
def _target_ok(path: str) -> str | None:
    """Почему путь нельзя устанавливать; None — можно."""
    if path in INFO:
        return None
    if path.startswith("references/"):
        return None if path.lower().endswith(REF_EXTS) else "в справочниках — только csv/xlsx/xls/json/yaml/txt"
    if path.startswith("config/rules/"):
        if "/" in path[len("config/rules/"):] or not re.fullmatch(r"[A-Za-z0-9_\-]+\.ya?ml",
                                                                    posixpath.basename(path)):
            return "в config/rules/ — только файлы *.yaml без подпапок"
        return None
    if path in ALLOWED_CONFIG:
        return None
    if path.startswith("rates/"):
        return None if path.lower().endswith(RATE_EXTS) else "в rates/ — только xml/xlsx/xls/csv"
    if path == "config/system.yaml":
        return "config/system.yaml архивом не меняется (настройки контура и доступа)"
    return "этот путь не обновляется с носителя"


def _parse_sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if not m:
            raise UpdateError(f"SHA256SUMS, строка {n}: ожидается «<sha256>  <путь>»")
        path = m.group(2).strip().replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        if path in out:
            raise UpdateError(f"SHA256SUMS: файл {path} указан дважды")
        out[path] = m.group(1).lower()
    if not out:
        raise UpdateError("SHA256SUMS пустой")
    return out


def _read_zip(data: bytes) -> dict[str, bytes]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"это не архив zip или он повреждён: {exc}") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise UpdateError(f"в архиве слишком много файлов ({len(infos)} > {MAX_FILES})")
    names = [i.filename.replace("\\", "/") for i in infos]
    # архив папки «update-2026-11/…» — снимаем общую верхнюю папку
    prefix = ""
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and all("/" in n for n in names) and f"{next(iter(tops))}/{SUMS}" in names:
        prefix = next(iter(tops)) + "/"
    files: dict[str, bytes] = {}
    total = 0
    for info, name in zip(infos, names):
        path = name[len(prefix):] if prefix else name
        parts = path.split("/")
        if path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in parts or "" in parts:
            raise UpdateError(f"небезопасный путь в архиве: {name}")
        if info.flag_bits & 0x1:
            raise UpdateError(f"зашифрованный файл в архиве: {name}")
        total += info.file_size
        if total > MAX_TOTAL:
            raise UpdateError(f"архив больше {MAX_TOTAL // 2**20} МБ после распаковки")
        with zf.open(info) as fh:
            chunk = fh.read(MAX_TOTAL + 1)
        if len(chunk) > MAX_TOTAL:
            raise UpdateError(f"файл больше {MAX_TOTAL // 2**20} МБ: {name}")
        files[path] = chunk
    return files


def _check_signature(sums: bytes, sig: bytes | None, st: dict) -> dict:
    keys = trusted_keys(st["keys_dir"])
    res = {"signed": sig is not None, "valid": False, "key": "", "required": st["require_signature"],
           "trusted_keys": len(keys)}
    if sig is None:
        if st["require_signature"]:
            raise UpdateError("архив без подписи (SHA256SUMS.sig), а в настройках "
                              "offline.update.require_signature: true")
        return res
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:                                   # pragma: no cover
        raise UpdateError("подпись не проверить: нет пакета cryptography") from exc
    raw = sig.strip()
    if len(raw) != 64:
        try:
            raw = base64.b64decode(raw, validate=True)
        except ValueError as exc:
            raise UpdateError("SHA256SUMS.sig: ожидается подпись Ed25519 (64 байта или base64)") from exc
    if not keys:
        raise UpdateError(f"архив подписан, но доверенных ключей нет (папка {st['keys_dir']}, файлы *.pub)")
    for name, pub in keys.items():
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(raw, sums)
        except (InvalidSignature, ValueError):
            continue
        res.update(valid=True, key=name)
        return res
    raise UpdateError("подпись SHA256SUMS.sig не подходит ни к одному доверенному ключу — "
                      "архив изменён или подписан чужим ключом")


def _check_content(path: str, data: bytes) -> str | None:
    if path.lower().endswith((".yaml", ".yml")):
        try:
            doc = yaml.safe_load(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            return f"YAML не читается: {str(exc).splitlines()[0][:160]}"
        if path.startswith("config/") and not isinstance(doc, dict):
            return "в файле настроек ожидается словарь «ключ: значение»"
    elif path.lower().endswith(".json"):
        try:
            json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            return f"JSON не читается: {exc}"
    elif path.lower().endswith((".csv", ".txt")) and path.startswith("references/"):
        if not data.strip():
            return "пустой файл"
    return None


def verify(data: bytes, filename: str = "update.zip", cfg: dict | None = None) -> dict:
    """Проверить архив, ничего не меняя. -> отчёт {ok, files, signature, errors, …}."""
    return _verify(data, filename, cfg)[0]


def _verify(data: bytes, filename: str, cfg: dict | None) -> tuple[dict, dict[str, bytes]]:
    st = update_settings(cfg)
    payload: dict[str, bytes] = {}
    report = {"archive": os.path.basename(filename or "update.zip"),
              "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
              "ok": False, "files": [], "errors": [], "signature": {}, "description": ""}
    try:
        files = _read_zip(data)
        if SUMS not in files:
            raise UpdateError("в архиве нет SHA256SUMS — список контрольных сумм обязателен")
        sums = _parse_sums(files[SUMS].decode("utf-8-sig"))
        report["signature"] = _check_signature(files[SUMS], files.get(SIG), st)
        for info in INFO:
            if info in files and info != "MANIFEST.json":
                report["description"] = files[info].decode("utf-8", "replace")[:2000]
        payload = {p: d for p, d in files.items() if p not in (SUMS, SIG)}
        for p in sorted(set(payload) - set(sums)):
            report["errors"].append(f"{p}: файла нет в SHA256SUMS")
        for p in sorted(set(sums) - set(payload)):
            report["errors"].append(f"{p}: указан в SHA256SUMS, но в архиве его нет")
        for p in sorted(set(payload) & set(sums)):
            got = hashlib.sha256(payload[p]).hexdigest()
            item = {"path": p, "size": len(payload[p]), "sha256": got, "ok": got == sums[p],
                    "action": ("курсы" if p.startswith("rates/") else
                               "описание" if p in INFO else
                               "заменить" if os.path.exists(os.path.join(PROJECT_ROOT, p)) else "добавить")}
            if got != sums[p]:
                item["error"] = "контрольная сумма не совпадает (файл повреждён или изменён)"
            else:
                why = _target_ok(p) or _check_content(p, payload[p])
                if why:
                    item["ok"], item["error"] = False, why
            if item.get("error"):
                report["errors"].append(f"{p}: {item['error']}")
            report["files"].append(item)
        report["ok"] = not report["errors"] and any(f["action"] != "описание" for f in report["files"])
        if not report["errors"] and not report["ok"]:
            report["errors"].append("в архиве нечего устанавливать")
    except UpdateError as exc:
        report["errors"].append(str(exc))
    return report, payload


# ===================================================================== установка
def _validate_after() -> None:
    """После замены: настройки, правила и встроенные справочники должны читаться."""
    from app.config_loader import load_config, load_rules
    from app.services.loader import load_sources
    cfg = load_config()
    for cid, c in (cfg.get("checks") or {}).items():
        load_rules(c.get("rules", f"{cid}.yaml"))
    load_sources({}, cfg)


def apply(data: bytes, filename: str = "update.zip", user: str = "-", cfg: dict | None = None) -> dict:
    """Проверить и установить архив. Не прошёл проверку — UpdateError, ничего не меняется."""
    report, payload = _verify(data, filename, cfg)
    if not report["ok"]:
        log("media_update", user, f"Обновление с носителя «{report['archive']}» отклонено: "
                                  f"{'; '.join(report['errors'][:3])}",
            meta={"archive": report["archive"], "sha256": report["sha256"], "ok": False,
                  "errors": report["errors"][:50]})
        raise UpdateError("; ".join(report["errors"][:5]))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(UPDATES_DIR, stamp, "backup")
    replaced: list[tuple[str, str | None]] = []           # (путь, копия прежней версии)
    rate_files: list[tuple[bytes, str]] = []
    try:
        for item in report["files"]:
            p = item["path"]
            if item["action"] == "описание":
                continue
            if item["action"] == "курсы":
                rate_files.append((payload[p], posixpath.basename(p)))
                continue
            dest = os.path.join(PROJECT_ROOT, *p.split("/"))
            old = None
            if os.path.exists(dest):
                old = os.path.join(backup_dir, *p.split("/"))
                os.makedirs(os.path.dirname(old), exist_ok=True)
                shutil.copy2(dest, old)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".tmp-update"
            with open(tmp, "wb") as fh:
                fh.write(payload[p])
            os.replace(tmp, dest)
            replaced.append((dest, old))
        _validate_after()
    except Exception as exc:  # noqa: BLE001 — откат при любой ошибке
        for dest, old in reversed(replaced):
            try:
                if old:
                    shutil.copy2(old, dest)
                else:
                    os.remove(dest)
            except OSError:
                pass
        log("media_update", user, f"Обновление с носителя «{report['archive']}» не установлено, "
                                  f"файлы возвращены: {exc}",
            meta={"archive": report["archive"], "sha256": report["sha256"], "ok": False,
                  "errors": [str(exc)]})
        why = (f"не удалось записать файл (нет прав или места на диске): {exc}" if isinstance(exc, OSError)
               else f"после замены файлов программа не читает настройки: {exc}")
        raise UpdateError(f"{why} — всё возвращено назад") from exc
    rates_res = []
    if rate_files:
        from app.rates.files import import_files
        rates_res = import_files(rate_files, user=user)
    report["installed"] = [i["path"] for i in report["files"] if i["action"] in ("заменить", "добавить")]
    report["rates"] = rates_res
    report["backup"] = os.path.relpath(backup_dir, PROJECT_ROOT) if os.path.isdir(backup_dir) else ""
    sig = report["signature"]
    sig_text = (f"подпись верна (ключ {sig['key']})" if sig.get("valid") else "без подписи")
    log("media_update", user,
        f"Обновление с носителя «{report['archive']}»: файлов {len(report['installed'])}"
        + (f", файлов курсов {len(rates_res)}" if rates_res else "") + f"; контрольные суммы совпали, {sig_text}",
        meta={"archive": report["archive"], "sha256": report["sha256"], "ok": True,
              "files": [{k: f[k] for k in ("path", "sha256", "action")} for f in report["files"]],
              "signature": sig, "backup": report["backup"],
              "rates": [{k: r.get(k) for k in ("file", "ok", "format", "error")} for r in rates_res]})
    report["applied"] = True
    return report


def history(limit: int = 50) -> list[dict]:
    """Последние обновления с носителя (из журнала)."""
    from app import journal
    return journal.query(kind="media_update", limit=limit)


# ===================================================================== сборка архива
def build_package(files: dict[str, bytes], description: str = "", sign_key_pem: bytes | None = None) -> bytes:
    """Собрать архив обновления: {путь в архиве: байты} -> zip с SHA256SUMS (и подписью)."""
    buf = io.BytesIO()
    entries = dict(files)
    if description:
        entries["UPDATE.txt"] = description.encode("utf-8")
    sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {p}\n" for p, d in sorted(entries.items())).encode()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, d in sorted(entries.items()):
            zf.writestr(p, d)
        zf.writestr(SUMS, sums)
        if sign_key_pem:
            from cryptography.hazmat.primitives import serialization
            key = serialization.load_pem_private_key(sign_key_pem, password=None)
            zf.writestr(SIG, base64.b64encode(key.sign(sums)) + b"\n")
    return buf.getvalue()
