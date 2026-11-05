#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SibDoc3: закрытый контур — архив «Обновление с носителя» (справочники, правила, курсы).

Собирается на любом компьютере (интернет не нужен, если файлы уже есть), переносится
на флешке и загружается в SibDoc: «Обновление с носителя» → «Проверить» → «Установить».

  python3 tools/make_update_package.py --out update-2026-11.zip references/hs_codes.csv \\
          config/rules/price.yaml --rates ~/Downloads/XML_daily.xml --text "HS-коды на ноябрь"

Пути программы (references/…, config/rules/…, config/fields.yaml …) кладутся в архив как есть;
файлы курсов (--rates) — в папку rates/ архива. В архив пишется SHA256SUMS (обязательно).
Подпись Ed25519 (по желанию, нужен пакет cryptography):
  python3 tools/make_update_package.py --gen-key ключи/         # sibdoc-update.key + sibdoc-update.pub
  python3 tools/make_update_package.py --sign ключи/sibdoc-update.key --out update.zip …
Открытый ключ (*.pub) кладётся в контуре в config/update_keys/; с
offline.update.require_signature: true SibDoc принимает только подписанные архивы.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


def gen_key(folder: str) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    os.makedirs(folder, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kp = os.path.join(folder, "sibdoc-update.key")
    with open(kp, "wb") as fh:
        fh.write(priv)
    os.chmod(kp, 0o600)
    with open(os.path.join(folder, "sibdoc-update.pub"), "w", encoding="ascii") as fh:
        fh.write(base64.b64encode(pub).decode() + "\n")
    print(f"Закрытый ключ: {kp} (храните у себя, в контур не переносите)")
    print(f"Открытый ключ: {os.path.join(folder, 'sibdoc-update.pub')} -> в контуре config/update_keys/")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SibDoc 3: архив обновления справочников, правил и курсов")
    ap.add_argument("paths", nargs="*", help="файлы программы: references/…, config/rules/…, config/fields.yaml …")
    ap.add_argument("--rates", nargs="*", default=[], help="файлы курсов (XML ЦБ РФ, CFETS, шаблон)")
    ap.add_argument("--out", default="sibdoc-update.zip")
    ap.add_argument("--text", default="", help="описание обновления (UPDATE.txt)")
    ap.add_argument("--sign", default="", help="закрытый ключ Ed25519 (PEM) для подписи")
    ap.add_argument("--gen-key", metavar="ПАПКА", help="создать пару ключей подписи и выйти")
    a = ap.parse_args(argv)
    if a.gen_key:
        gen_key(a.gen_key)
        return 0
    from app import media_update
    files: dict[str, bytes] = {}
    for p in a.paths:
        full = os.path.abspath(p)
        rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
        if rel.startswith(".."):
            raise SystemExit(f"{p}: файл должен лежать внутри программы (references/…, config/…)")
        why = media_update._target_ok(rel)
        if why:
            raise SystemExit(f"{rel}: {why}")
        with open(full, "rb") as fh:
            files[rel] = fh.read()
    for p in a.rates:
        with open(p, "rb") as fh:
            files["rates/" + os.path.basename(p)] = fh.read()
    if not files:
        raise SystemExit("Нечего упаковывать: укажите файлы программы и/или --rates")
    key = open(a.sign, "rb").read() if a.sign else None
    data = media_update.build_package(files, a.text, key)
    with open(a.out, "wb") as fh:
        fh.write(data)
    print(f"Архив обновления: {a.out} — файлов {len(files)}, {'с подписью' if key else 'без подписи'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
