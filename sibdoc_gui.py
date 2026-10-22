# -*- coding: utf-8 -*-
"""SibDoc: окно приложения (pywebview) + встроенный сервер."""
import threading, time, urllib.request, os
import uvicorn


def run_server():
    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, log_level="warning")


threading.Thread(target=run_server, daemon=True).start()

for _ in range(60):
    try:
        if b'"status":"ok"' in urllib.request.urlopen(
                "http://localhost:8000/api/health", timeout=2).read():
            break
    except Exception:
        pass
    time.sleep(1)

import webview

window = webview.create_window("SibDoc", "http://localhost:8000",
                               min_size=(1280, 850))
window.events.closed += lambda: os._exit(0)
webview.start()
os._exit(0)
