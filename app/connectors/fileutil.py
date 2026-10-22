# -*- coding: utf-8 -*-
"""Файловые утилиты коннекторов: безопасные имена, кодировки (китайские GB2312/GBK,
русские cp1251/cp866), распаковка zip с защитой от zip-slip и zip-бомб."""
from __future__ import annotations

import fnmatch
import io
import posixpath
import re
import zipfile

# Китайские почтовые клиенты пишут charset=gb2312, а символы берут из GBK —
# декодируем надмножеством gb18030.
CHARSET_ALIASES = {
    "gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "cp936": "gb18030",
    "euc-cn": "gb18030", "gb_2312-80": "gb18030", "csgb2312": "gb18030",
    "x-euc-cn": "gb18030", "big5": "big5hkscs", "windows-1251": "cp1251",
    "win-1251": "cp1251", "koi8r": "koi8-r",
}

DEFAULT_MASKS = ["*.xlsx", "*.xls", "*.csv", "*.xml", "*.pdf", "*.zip"]
SLOT_EXTS = (".xlsx", ".xls", ".csv", ".xml")


def safe_filename(name: str, default: str = "file") -> str:
    """Только имя файла (без каталогов), без управляющих и запрещённых в Windows символов."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "_", name).strip().strip(".").strip()
    if not name:
        name = default
    if len(name.encode("utf-8")) > 200:
        stem, dot, ext = name.rpartition(".")
        if not dot or len(ext) > 10:
            stem, ext = name, ""
        while len((stem + "." + ext).encode("utf-8")) > 200 and stem:
            stem = stem[:-1]
        name = stem + ("." + ext if ext else "")
    return name


def _gb2312_share(text: str) -> float:
    """Доля не-ASCII символов, входящих в GB2312 (частые иероглифы)."""
    non_ascii = [ch for ch in text if ord(ch) > 127]
    if not non_ascii:
        return 1.0
    ok = 0
    for ch in non_ascii:
        try:
            ch.encode("gb2312")
            ok += 1
        except UnicodeEncodeError:
            pass
    return ok / len(non_ascii)


def smart_decode(raw: bytes, order=("utf-8", "gb18030", "cp1251")) -> str:
    """Декодировать байты неизвестной кодировки.

    gb18030 принимается, только если почти все символы — обычные иероглифы GB2312
    (иначе кириллица в cp1251/cp866 «декодируется» в редкие иероглифы)."""
    if isinstance(raw, str):
        return raw
    for enc in order:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if enc == "gb18030" and _gb2312_share(text) < 0.95:
            continue
        return text
    return raw.decode("latin-1")


def decode_with_charset(raw: bytes, charset: str | None) -> str:
    cs = (charset or "").strip().lower()
    if cs and cs not in ("unknown-8bit", "us-ascii", "ascii"):
        cs = CHARSET_ALIASES.get(cs, cs)
        try:
            return raw.decode(cs)
        except (UnicodeDecodeError, LookupError):
            pass
    return smart_decode(raw)


def fix_surrogates(text: str) -> str:
    """Строка с «сырыми» 8-битными байтами (surrogateescape) -> нормальный текст."""
    if not any("\udc80" <= ch <= "\udcff" for ch in text):
        return text
    return smart_decode(text.encode("utf-8", "surrogateescape"))


def zip_entry_name(info: zipfile.ZipInfo) -> str:
    """Имя файла в zip: без флага UTF-8 Python читает cp437, а китайский Windows
    пишет GBK, русский — cp866."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    return smart_decode(raw, order=("utf-8", "gb18030", "cp866", "cp1251"))


def match_masks(name: str, masks) -> bool:
    if not masks:
        return True
    if isinstance(masks, str):
        masks = [masks]
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, str(m).lower()) for m in masks)


def extract_zip(data: bytes, *, max_total: int = 200 * 2**20, max_file: int = 100 * 2**20,
                max_files: int = 1000, max_ratio: int = 300) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Распаковать zip в память. -> ([(имя, байты)], [заметки о пропущенном]).

    Защита:
      * zip-slip — используется только базовое имя; абсолютные пути и '..' пропускаются;
      * zip-бомбы — лимиты на число файлов, размер файла, суммарный размер и степень сжатия;
        размер считается по реально прочитанным байтам, а не по заголовку архива;
      * зашифрованные записи и вложенные архивы пропускаются.
    ValueError — архив повреждён или превышен общий лимит.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"повреждённый zip: {exc}") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > max_files:
        raise ValueError(f"в архиве слишком много файлов ({len(infos)} > {max_files})")
    entries: list[tuple[str, bytes]] = []
    notes: list[str] = []
    total = 0
    for info in infos:
        name = zip_entry_name(info)
        norm = name.replace("\\", "/")
        parts = norm.split("/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in parts:
            notes.append(f"небезопасный путь в архиве пропущен: {name}")
            continue
        if info.flag_bits & 0x1:
            notes.append(f"зашифрованный файл пропущен: {name}")
            continue
        if info.file_size > max_file:
            notes.append(f"слишком большой файл в архиве пропущен: {name}")
            continue
        if info.compress_size and info.file_size > 2**20 \
                and info.file_size / info.compress_size > max_ratio:
            notes.append(f"подозрение на zip-бомбу (сжатие > {max_ratio}x), пропущен: {name}")
            continue
        if total + info.file_size > max_total:
            raise ValueError(f"превышен лимит распаковки {max_total // 2**20} МБ")
        with zf.open(info) as fh:
            chunk = fh.read(max_file + 1)
        if len(chunk) > max_file:
            notes.append(f"размер в заголовке занижен, файл пропущен: {name}")
            continue
        total += len(chunk)
        if total > max_total:
            raise ValueError(f"превышен лимит распаковки {max_total // 2**20} МБ")
        base = safe_filename(posixpath.basename(norm))
        if base.lower().endswith((".zip", ".rar", ".7z")):
            notes.append(f"вложенный архив пропущен: {name}")
            continue
        entries.append((base, chunk))
    return entries, notes


def csv_to_utf8(data: bytes) -> tuple[bytes, str]:
    """CSV брокера в GBK/GB18030 или cp1251 -> UTF-8 с BOM. -> (байты, исходная кодировка)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data, "utf-8-sig"
    try:
        data.decode("utf-8")
        return data, "utf-8"
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "cp1251", "gb18030!"):
        strict = not enc.endswith("!")
        enc = enc.rstrip("!")
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        if strict and enc == "gb18030" and _gb2312_share(text) < 0.95:
            continue
        return b"\xef\xbb\xbf" + text.encode("utf-8"), enc
    return data, "unknown"


def unique_name(name: str, used: set | None) -> str:
    """Не перезаписывать файл, уже сохранённый в этом же заборе."""
    if used is None:
        return name
    cand, n = name, 2
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    while cand.lower() in used:
        cand = f"{stem}_{n}" + (f".{ext}" if ext else "")
        n += 1
    used.add(cand.lower())
    return cand
