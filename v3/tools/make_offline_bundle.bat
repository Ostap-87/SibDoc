@echo off
REM ============================================================
REM  SibDoc3: zakrytyy kontur - komplekt dlya ustanovki bez interneta (Windows)
REM  Zapuskat na kompyutere S INTERNETOM. Parametry - kak u tools\make_offline_bundle.py:
REM    tools\make_offline_bundle.bat                       (dlya etogo kompyutera + Tesseract + Python)
REM    tools\make_offline_bundle.bat --platforms windows --python 3.11,3.12
REM    tools\make_offline_bundle.bat --dry-run
REM  Rezultat: dist\sibdoc3-offline-GGGGMMDD\ i arhiv .zip. Podrobno - docs\OFFLINE.md
REM ============================================================
cd /d "%~dp0\.."
set PY=python
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
where py >nul 2>nul && if not exist .venv\Scripts\python.exe set PY=py -3
if "%~1"=="" (
  %PY% tools\make_offline_bundle.py --platforms native --with-ocr --with-python
) else (
  %PY% tools\make_offline_bundle.py %*
)
pause
