#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SibDoc3: закрытый контур — комплект для установки без интернета.

Запускается на компьютере С ИНТЕРНЕТОМ. Собирает одну папку (и архив), которую
переносят в закрытый контур на флешке:

  <имя>/                       программа SibDoc 3 (как в репозитории)
  <имя>/wheelhouse/<платформа>-py<версия>/   библиотеки Python (*.whl) для pip install --no-index
  <имя>/wheelhouse/any/        библиотеки без готовых wheel (собраны здесь, чистый Python)
  <имя>/assets/fonts/cjk.ttc   китайский шрифт для PDF (если найден на этом компьютере)
  <имя>/offline/ocr/…          Tesseract: установщик для Windows, пакеты conda-forge для Mac/Linux
  <имя>/offline/python/…       установщики Python для Windows и Mac (по желанию)
  <имя>/offline/docker/…       образ Docker (docker save) — по желанию
  <имя>/offline/MANIFEST.json  что внутри; <имя>/SHA256SUMS — контрольные суммы всех файлов
  <имя>/ЗАКРЫТЫЙ-КОНТУР.txt    что делать в контуре

Примеры:
  python3 tools/make_offline_bundle.py                         # для этого компьютера (надёжнее всего)
  python3 tools/make_offline_bundle.py --platforms windows,linux-x86_64 --python 3.11,3.12
  python3 tools/make_offline_bundle.py --platforms all --with-ocr --with-python 3.12.10 --with-docker
  python3 tools/make_offline_bundle.py --dry-run --platforms all   # только показать, что будет сделано
  python3 tools/make_offline_bundle.py --check /media/usb/sibdoc3-offline-20261105   # проверить комплект

Платформы: native (этот компьютер, по умолчанию), linux-x86_64, linux-aarch64, windows,
mac-arm64, mac-x86_64, all. Для чужой платформы pip скачивает только готовые wheel
(--platform … --only-binary=:all:); зависимости с условием по ОС (colorama, pythonnet,
pyobjc…) добавляются отдельным списком EXTRAS. Надёжнее всего собирать комплект на
компьютере с той же ОС, что и в контуре (режим native), — и проверить установку там же.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

PLATFORMS = {
    # имя: (теги pip --platform по порядку предпочтения, ОС, платформа conda-forge)
    "linux-x86_64": (["manylinux2014_x86_64", "manylinux_2_28_x86_64"], "linux", "linux-64"),
    "linux-aarch64": (["manylinux2014_aarch64", "manylinux_2_28_aarch64"], "linux", "linux-aarch64"),
    "windows": (["win_amd64"], "windows", "win-64"),
    "mac-arm64": (["macosx_11_0_arm64"], "mac", "osx-arm64"),
    "mac-x86_64": (["macosx_11_0_x86_64"], "mac", "osx-64"),
}
# pip при --platform проверяет условия «sys_platform == …» по ЭТОМУ компьютеру — зависимости,
# нужные только на другой ОС, докачиваются явно (ошибка по одному пакету — предупреждение).
EXTRAS = {
    "windows": ["colorama", "pythonnet", "clr_loader", "cffi", "pycparser"],
    "mac": ["pyobjc-core", "pyobjc-framework-Cocoa", "pyobjc-framework-Quartz",
            "pyobjc-framework-WebKit", "pyobjc-framework-Security",
            "pyobjc-framework-UniformTypeIdentifiers", "pyobjc-framework-Vision"],
    "linux": [],
}
# пакеты без готовых wheel на PyPI (только исходники, чистый Python): собираются здесь
SDIST_ONLY = ["proxy_tools"]

DEFAULT_PYTHON = "3.12"
PYTHON_INSTALLER = {
    "windows": "https://www.python.org/ftp/python/{v}/python-{v}-amd64.exe",
    "mac": "https://www.python.org/ftp/python/{v}/python-{v}-macos11.pkg",
}
# Tesseract для Windows — установщик UB Mannheim (актуальная ссылка:
# https://github.com/UB-Mannheim/tesseract/wiki); можно задать --tesseract-win-url
TESSERACT_WIN_URL = ("https://github.com/UB-Mannheim/tesseract/releases/download/"
                     "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe")
MICROMAMBA_URL = "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-{plat}"
CONDA_CHANNEL = "https://conda.anaconda.org/conda-forge"
# Китайский шрифт для PDF: ищем на машине сборки комплекта (Linux, macOS, Windows).
# В git шрифт не хранится (v3/.gitignore → assets/fonts/cjk.*): он попадает в офлайн-комплект здесь.
CJK_FONTS = [
    # свой шрифт проекта (Noto Sans SC, лицензия OFL) — в приоритете: его можно распространять
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts", "cjk.ttf"),
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",       # macOS
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",                                  # Windows
    "C:/Windows/Fonts/simsun.ttc",
]
IMAGE = "postcontrol-suite:latest"
EXCLUDE_DIRS = {".git", ".venv", "storage", "__pycache__", "dist", "wheelhouse", "vendor", ".ocr",
                ".ocr-tmp", ".pytest_cache", "offline", "node_modules"}
README_NAME = "ЗАКРЫТЫЙ-КОНТУР.txt"


class Plan:
    """Исполнитель шагов: в режиме --dry-run только печатает и запоминает их."""

    def __init__(self, dry: bool):
        self.dry = dry
        self.steps: list[str] = []
        self.warnings: list[str] = []

    def note(self, text: str) -> None:
        self.steps.append(text)
        print(("[план] " if self.dry else "  ") + text, flush=True)

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        print("  ВНИМАНИЕ: " + text, flush=True)

    def run(self, cmd: list[str], *, env: dict | None = None, must: bool = True) -> bool:
        self.note("$ " + " ".join(_q(_rel(c)) for c in cmd))
        if self.dry:
            return True
        res = subprocess.run(cmd, env={**os.environ, **(env or {})})
        if res.returncode != 0:
            if must:
                raise SystemExit(f"ОШИБКА: команда завершилась с кодом {res.returncode}: {cmd[0]} …")
            self.warn(f"не выполнено (код {res.returncode}): {' '.join(cmd[:6])} …")
            return False
        return True

    def download(self, url: str, dest: str, must: bool = False) -> bool:
        self.note(f"скачать {url} -> {_rel(dest)}")
        if self.dry:
            return True
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, open(dest + ".part", "wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
            os.replace(dest + ".part", dest)
            return True
        except Exception as exc:  # noqa: BLE001
            if os.path.exists(dest + ".part"):
                os.remove(dest + ".part")
            if must:
                raise SystemExit(f"ОШИБКА: не скачалось {url}: {exc}") from exc
            self.warn(f"не скачалось {url}: {exc}")
            return False


def _q(s: str) -> str:
    return s if s and all(c.isalnum() or c in "-_./:=,+@%" for c in s) else repr(s)


_OUT = [""]


def _rel(p: str) -> str:
    base = _OUT[0]
    return os.path.relpath(p, base) if base and p.startswith(base) else p


def native_name() -> str:
    sysname = {"Linux": "linux", "Darwin": "mac", "Windows": "windows"}.get(platform.system(), "other")
    mach = platform.machine().lower()
    mach = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64" if sysname == "mac" else "aarch64"}.get(mach, mach)
    if sysname == "windows":
        return "windows"
    return f"{sysname}-{mach}"


def host_conda_platform() -> str | None:
    n = native_name()
    return PLATFORMS[n][2] if n in PLATFORMS and PLATFORMS[n][1] != "windows" else None


def parse_platforms(value: str) -> list[str]:
    items = [x.strip().lower() for x in (value or "native").split(",") if x.strip()]
    out: list[str] = []
    for it in items:
        if it == "all":
            out += [p for p in PLATFORMS if p not in out]
        elif it in ("native", "this", "этот"):
            if "native" not in out:
                out.append("native")
        elif it in PLATFORMS:
            if it not in out:
                out.append(it)
        else:
            raise SystemExit(f"Неизвестная платформа «{it}». Допустимо: native, all, {', '.join(PLATFORMS)}")
    return out


# --------------------------------------------------------------------- программа
def app_files(src: str) -> list[str]:
    """Файлы программы: из git (без игнорируемых), иначе обход папки с исключениями."""
    if os.path.isdir(os.path.join(src, ".git")) or os.path.isfile(os.path.join(src, ".git")):
        try:
            out = subprocess.run(["git", "-C", src, "ls-files", "-z", "--cached", "--others",
                                  "--exclude-standard"], capture_output=True, check=True).stdout
            files = [f for f in out.decode("utf-8").split("\0") if f]
            return sorted(f for f in files if os.path.isfile(os.path.join(src, f))
                          and not set(f.split("/")[:-1]) & EXCLUDE_DIRS)
        except (OSError, subprocess.CalledProcessError):
            pass
    files = []
    for base, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in names:
            if n.endswith((".pyc", ".DS_Store")):
                continue
            files.append(os.path.relpath(os.path.join(base, n), src).replace(os.sep, "/"))
    return sorted(files)


def copy_app(plan: Plan, src: str, dest: str) -> int:
    files = app_files(src)
    plan.note(f"скопировать программу: {len(files)} файлов из {src}")
    if plan.dry:
        return len(files)
    for f in files:
        to = os.path.join(dest, *f.split("/"))
        os.makedirs(os.path.dirname(to), exist_ok=True)
        shutil.copy(os.path.join(src, *f.split("/")), to)   # copy: copystat падает в песочнице (chflags)
    return len(files)


# --------------------------------------------------------------------- библиотеки
def build_wheelhouse(plan: Plan, src: str, wh: str, platforms: list[str], pythons: list[str]) -> list[str]:
    req = os.path.join(src, "requirements.txt")
    pip = [sys.executable, "-m", "pip"]
    dirs = []
    cross = [p for p in platforms if p != "native"]
    any_dir = os.path.join(wh, "any")
    if not plan.dry:
        os.makedirs(any_dir, exist_ok=True)
    if cross:
        plan.run(pip + ["wheel", "--no-deps", "-w", any_dir] + SDIST_ONLY)
    for p in platforms:
        if p == "native":
            name = f"{native_name()}-py{sys.version_info.major}.{sys.version_info.minor}"
            out = os.path.join(wh, name)
            if not plan.dry:
                os.makedirs(out, exist_ok=True)      # pip не всегда создаёт папку сам (путь с пробелами)
            plan.run(pip + ["wheel", "-r", req, "-w", out])
            dirs.append(name)
            continue
        tags, osname, _ = PLATFORMS[p]
        for py in pythons:
            name = f"{p}-py{py}"
            out = os.path.join(wh, name)
            if not plan.dry:
                os.makedirs(out, exist_ok=True)
            base = pip + ["download", "--only-binary=:all:", "--implementation", "cp",
                          "--python-version", py, "--find-links", any_dir, "-d", out]
            for t in tags:
                base += ["--platform", t]
            plan.run(base + ["-r", req])
            for extra in EXTRAS[osname]:
                plan.run(base + [extra], must=False)
            dirs.append(name)
    return dirs


# --------------------------------------------------------------------- шрифты, OCR, Python, Docker
def add_cjk_font(plan: Plan, dest_app: str, font: str | None) -> str:
    cand = [font] if font else CJK_FONTS
    for path in cand:
        if path and os.path.exists(path):
            to = os.path.join(dest_app, "assets", "fonts", "cjk.ttc" if path.endswith(".ttc") else "cjk.ttf")
            plan.note(f"китайский шрифт для PDF: {path} -> {_rel(to)}")
            if not plan.dry:
                os.makedirs(os.path.dirname(to), exist_ok=True)
                shutil.copy(path, to)      # copy: copystat падает в песочнице (chflags)
            return os.path.basename(path)
    plan.warn("китайский шрифт (WenQuanYi Zen Hei) на этом компьютере не найден — PDF возьмёт "
              "системный шрифт в контуре (Windows/Mac) или встроенный STSong-Light")
    return ""


def add_ocr(plan: Plan, off: str, platforms: list[str], win_url: str) -> list[str]:
    targets = []
    for p in platforms:
        name = native_name() if p == "native" else p
        if name in PLATFORMS and name not in targets:
            targets.append(name)
    done = []
    host = host_conda_platform()
    mm_host = None
    for name in targets:
        _, osname, conda_plat = PLATFORMS[name]
        dest = os.path.join(off, "ocr", name)
        if osname == "windows":
            if plan.download(win_url, os.path.join(dest, os.path.basename(win_url))):
                done.append(name)
            continue
        if host is None:
            plan.warn(f"OCR для {name}: пакеты conda-forge собираются только на Linux или Mac")
            continue
        if mm_host is None:
            mm_host = os.path.join(tempfile.gettempdir() if plan.dry else tempfile.mkdtemp(prefix="sibdoc-mm-"),
                                   "micromamba")
            if not plan.download(MICROMAMBA_URL.format(plat=host), mm_host, must=True):
                continue
            if not plan.dry:
                os.chmod(mm_host, 0o755)
        mm_target = os.path.join(dest, "micromamba")
        if not plan.download(MICROMAMBA_URL.format(plat=conda_plat), mm_target):
            continue
        if not plan.dry:
            os.chmod(mm_target, 0o755)
        root = os.path.join(dest, "root")
        env_tmp = os.path.join(root, "_tmp_env")
        ok = plan.run([mm_host, "create", "-y", "-q", "-p", env_tmp, "--platform", conda_plat,
                       "--override-channels", "-c", CONDA_CHANNEL, "tesseract"],
                      env={"MAMBA_ROOT_PREFIX": root}, must=False)
        plan.note(f"удалить временное окружение {_rel(env_tmp)} (пакеты остаются в {_rel(root)}/pkgs)")
        if not plan.dry:
            shutil.rmtree(env_tmp, ignore_errors=True)
            with open(os.path.join(dest, "channel.txt"), "w", encoding="utf-8") as fh:
                fh.write(CONDA_CHANNEL + "\n")
        if ok:
            done.append(name)
    return done


def add_python(plan: Plan, off: str, platforms: list[str], version: str) -> list[str]:
    oses = []
    for p in platforms:
        name = native_name() if p == "native" else p
        osname = PLATFORMS.get(name, (None, None, None))[1]
        if osname in PYTHON_INSTALLER and osname not in oses:
            oses.append(osname)
    got = []
    for osname in oses:
        url = PYTHON_INSTALLER[osname].format(v=version)
        if plan.download(url, os.path.join(off, "python", os.path.basename(url))):
            got.append(os.path.basename(url))
    if not oses:
        plan.note("установщики Python: для Linux не нужны (python3 из дистрибутива, 3.10+)")
    return got


def add_docker(plan: Plan, src: str, off: str) -> str:
    out = os.path.join(off, "docker", "postcontrol-suite.tar.gz")
    plan.run(["docker", "build", "-t", IMAGE, src])
    plan.note(f"$ docker save {IMAGE} | gzip > {_rel(out)}")
    if plan.dry:
        return os.path.basename(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    proc = subprocess.Popen(["docker", "save", IMAGE], stdout=subprocess.PIPE)
    with gzip.open(out, "wb", compresslevel=6) as gz:
        shutil.copyfileobj(proc.stdout, gz, 1 << 20)
    if proc.wait() != 0:
        raise SystemExit("ОШИБКА: docker save не выполнен")
    return os.path.basename(out)


# --------------------------------------------------------------------- описание, суммы, архив
README = """SibDoc 3 — комплект для закрытого контура (без интернета)
=========================================================
Собран: {created} · платформы: {platforms} · Python: {pythons}

0. Проверить, что комплект не повреждён (по желанию):
     Linux:   sha256sum -c SHA256SUMS
     Mac:     shasum -a 256 -c SHA256SUMS
     любая ОС с Python: python3 tools/make_offline_bundle.py --check .

1. Python (если его нет): offline/python/ — установщик для Windows (.exe, отметьте
   «Add python.exe to PATH») или Mac (.pkg). В Linux — python3 из дистрибутива (3.10+).

2. Установка SibDoc (библиотеки — из папки wheelhouse, интернет не нужен):
     Linux:    bash install.sh --offline wheelhouse
     Windows:  install.bat --offline wheelhouse
     Mac:      bash install_mac.command --offline wheelhouse
   Docker (если в комплекте есть offline/docker/postcontrol-suite.tar.gz):
     docker load -i offline/docker/postcontrol-suite.tar.gz
     bash install.sh --offline wheelhouse   (найдёт загруженный образ и запустит контейнер)

3. Распознавание сканов (OCR) — работает без Tesseract:
     Mac:      системный Vision (ставится из комплекта: requirements-mac.txt) — ru/en/zh
     Windows:  системный движок Windows 10/11 (пакет winsdk) — язык зависит от языковых пакетов
     Linux:    нужен Tesseract (если в комплекте есть offline/ocr — он там; иначе apt install tesseract-ocr)
   Если в комплекте есть offline/ocr — можно поставить и Tesseract:
     Windows:  запустите offline/ocr/windows/tesseract-ocr-w64-setup-*.exe (путь по умолчанию)
     Mac/Linux: bash install_ocr_mac.command --offline offline/ocr/<платформа>
   Языки rus / eng / chi_sim уже лежат в папке tessdata программы.

4. Запуск: start.sh / start.bat / значок SibDoc (Mac). В шапке — «Закрытый контур:
   интернет не используется». Курсы валют — файлами («Курсы валют» → «Курсы из файла»),
   справочники и правила — «Обновление с носителя». Подробно — docs/OFFLINE.md.
"""


def write_sums(folder: str) -> int:
    lines = []
    for base, dirs, names in os.walk(folder):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(base, n)
            rel = os.path.relpath(p, folder).replace(os.sep, "/")
            if rel == "SHA256SUMS":
                continue
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            lines.append(f"{h.hexdigest()}  {rel}\n")
    with open(os.path.join(folder, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(lines)
    return len(lines)


def check_sums(folder: str) -> tuple[int, list[str]]:
    """Проверить SHA256SUMS папки. -> (проверено файлов, ошибки)."""
    path = os.path.join(folder, "SHA256SUMS")
    if not os.path.isfile(path):
        return 0, [f"нет файла {path}"]
    errors, n = [], 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            digest, _, rel = line.partition("  ")
            p = os.path.join(folder, *rel.split("/"))
            n += 1
            if not os.path.isfile(p):
                errors.append(f"нет файла: {rel}")
                continue
            h = hashlib.sha256()
            with open(p, "rb") as f2:
                for chunk in iter(lambda: f2.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                errors.append(f"не совпадает контрольная сумма: {rel}")
    return n, errors


def make_archive(folder: str, fmt: str) -> str:
    base = os.path.basename(folder.rstrip(os.sep))
    parent = os.path.dirname(folder.rstrip(os.sep))
    if fmt == "tar.gz":
        out = os.path.join(parent, base + ".tar.gz")
        with tarfile.open(out, "w:gz") as tf:
            tf.add(folder, arcname=base)
        return out
    out = os.path.join(parent, base + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for b, dirs, names in os.walk(folder):
            dirs.sort()
            for n in sorted(names):
                p = os.path.join(b, n)
                arc = base + "/" + os.path.relpath(p, folder).replace(os.sep, "/")
                info = zipfile.ZipInfo.from_file(p, arc)
                mode = os.stat(p).st_mode
                if n.endswith((".sh", ".command")) or mode & stat.S_IXUSR:
                    mode |= 0o755
                info.external_attr = (mode & 0xFFFF) << 16          # права (исполняемые .sh/.command)
                info.compress_type = zipfile.ZIP_STORED if n.endswith((".whl", ".gz", ".zip", ".exe", ".pkg",
                                                                       ".conda", ".bz2")) else zipfile.ZIP_DEFLATED
                with open(p, "rb") as fh:
                    zf.writestr(info, fh.read())
    return out


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SibDoc 3: комплект для установки в закрытом контуре (без интернета)")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="куда положить комплект (по умолчанию dist/)")
    ap.add_argument("--name", default="", help="имя папки комплекта (по умолчанию sibdoc3-offline-ГГГГММДД)")
    ap.add_argument("--src", default=ROOT, help="папка программы (по умолчанию — этот репозиторий)")
    ap.add_argument("--platforms", default="native",
                    help="native | all | " + ", ".join(PLATFORMS) + " (через запятую)")
    ap.add_argument("--python", default=DEFAULT_PYTHON, help="версии Python для чужих платформ: 3.11,3.12")
    ap.add_argument("--wheelhouse", default="", help="готовая папка wheel (не скачивать, а взять её)")
    ap.add_argument("--with-ocr", action="store_true", help="Tesseract: установщик Windows, пакеты Mac/Linux")
    ap.add_argument("--tesseract-win-url", default=TESSERACT_WIN_URL)
    ap.add_argument("--with-python", nargs="?", const="3.12.10", default="",
                    help="положить установщики Python (версия, по умолчанию 3.12.10)")
    ap.add_argument("--with-docker", action="store_true", help="собрать образ и сохранить (docker save)")
    ap.add_argument("--cjk-font", default="", help="китайский шрифт TrueType для PDF (по умолчанию WenQuanYi)")
    ap.add_argument("--format", choices=("zip", "tar.gz", "none"), default="zip", help="архив комплекта")
    ap.add_argument("--dry-run", action="store_true", help="ничего не скачивать и не создавать — показать план")
    ap.add_argument("--check", metavar="ПАПКА", help="проверить контрольные суммы готового комплекта")
    ap.add_argument("--sums", metavar="ПАПКА",
                    help="пересчитать SHA256SUMS готового комплекта (после обрезки платформ или правок)")
    a = ap.parse_args(argv)

    if a.sums:
        n = write_sums(os.path.abspath(a.sums))
        print(f"Контрольные суммы пересчитаны: {n} файлов — {a.sums}/SHA256SUMS")
        return 0

    if a.check:
        n, errors = check_sums(a.check)
        for e in errors:
            print("  FAIL " + e)
        print(f"Проверено файлов: {n}. " + ("Комплект цел." if not errors and n else "КОМПЛЕКТ ПОВРЕЖДЁН."))
        return 0 if not errors and n else 1

    platforms = parse_platforms(a.platforms)
    pythons = [p.strip() for p in a.python.split(",") if p.strip()]
    src = os.path.abspath(a.src)
    name = a.name or "sibdoc3-offline-" + dt.date.today().strftime("%Y%m%d")
    out_root = os.path.abspath(a.out)
    folder = os.path.join(out_root, name)
    _OUT[0] = folder
    plan = Plan(a.dry_run)
    print(f"SibDoc 3: комплект для закрытого контура -> {folder}" + (" (dry-run: только план)" if a.dry_run else ""))
    if not a.dry_run:
        if os.path.exists(folder):
            raise SystemExit(f"Папка уже есть: {folder} — удалите её или задайте --name")
        os.makedirs(folder)

    n_app = copy_app(plan, src, folder)
    wh = os.path.join(folder, "wheelhouse")
    if a.wheelhouse:
        plan.note(f"библиотеки — из готовой папки {a.wheelhouse}")
        if not a.dry_run:
            shutil.copytree(a.wheelhouse, wh)
        wh_dirs = sorted(d for d in os.listdir(a.wheelhouse) if os.path.isdir(os.path.join(a.wheelhouse, d))) \
            if os.path.isdir(a.wheelhouse) else []
    else:
        wh_dirs = build_wheelhouse(plan, src, wh, platforms, pythons)
    font = add_cjk_font(plan, folder, a.cjk_font or None)
    off = os.path.join(folder, "offline")
    ocr = add_ocr(plan, off, platforms, a.tesseract_win_url) if a.with_ocr else []
    pys = add_python(plan, off, platforms, a.with_python) if a.with_python else []
    docker = add_docker(plan, src, off) if a.with_docker else ""

    created = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = ""
    try:
        commit = subprocess.run(["git", "-C", src, "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    manifest = {"product": "SibDoc 3", "created": created, "git_commit": commit, "app_files": n_app,
                "platforms": platforms, "python": pythons, "wheelhouse": wh_dirs, "cjk_font": font,
                "ocr": ocr, "python_installers": pys, "docker_image": docker, "warnings": plan.warnings}
    plan.note(f"описание комплекта: offline/MANIFEST.json, {README_NAME}, SHA256SUMS")
    if a.dry_run:
        print(f"\nПлан: шагов {len(plan.steps)}, предупреждений {len(plan.warnings)}. Ничего не создано.")
        return 0
    if os.path.isdir(wh):
        for d in os.listdir(wh):
            n = len([f for f in os.listdir(os.path.join(wh, d)) if f.endswith(".whl")])
            if n == 0:
                plan.warn(f"в wheelhouse/{d} нет ни одного .whl")
    os.makedirs(off, exist_ok=True)
    with open(os.path.join(off, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, README_NAME), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(README.format(created=created, platforms=", ".join(platforms), pythons=", ".join(pythons)))
    n_sums = write_sums(folder)
    print(f"Контрольные суммы: {n_sums} файлов (SHA256SUMS)")
    if a.format != "none":
        arc = make_archive(folder, a.format)
        print(f"Архив: {arc} ({os.path.getsize(arc) / 2**20:.1f} МБ)")
    print(f"Готово: {folder}" + (f"; предупреждений: {len(plan.warnings)}" if plan.warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
