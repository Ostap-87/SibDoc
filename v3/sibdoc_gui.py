# -*- coding: utf-8 -*-
"""SibDoc: окно приложения (pywebview) + встроенный сервер на свободном порту.

Запуск: python3 sibdoc_gui.py  (на Mac — приложение SibDoc из «Программ», см. install_mac.command)
"""
import os
import socket
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import uvicorn  # noqa: E402


def free_port(preferred: int = 8000) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


PORT = free_port(int(os.environ.get("SIBDOC_PORT", "8000")))
URL = f"http://127.0.0.1:{PORT}"


def run_server():
    uvicorn.run("app.server:app", host="127.0.0.1", port=PORT, log_level="warning")


threading.Thread(target=run_server, daemon=True).start()

for _ in range(90):
    try:
        if b'"status":"ok"' in urllib.request.urlopen(URL + "/api/health", timeout=2).read():
            break
    except Exception:
        pass
    time.sleep(1)

try:
    import webview
except ImportError:                      # окна нет — открываем в браузере по умолчанию
    import webbrowser
    webbrowser.open(URL)
    print(f"SibDoc работает: {URL} (Ctrl+C — остановить)")
    while True:
        time.sleep(3600)

try:
    webview.settings["ALLOW_DOWNLOADS"] = True        # отчёты Excel/PDF сохраняются в «Загрузки»
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
except Exception:
    pass
window = webview.create_window("SibDoc", URL, width=1440, height=920, min_size=(1100, 720))
window.events.closed += lambda: os._exit(0)
webview.start()
os._exit(0)
