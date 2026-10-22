@echo off
REM ============================================================
REM  PostControl Suite - ustanovka i zapusk (Windows)
REM  Rabotaet BEZ interneta: vse zavisimosti v .\vendor
REM ============================================================
cd /d "%~dp0"
echo ===================================================
echo  PostControl Suite - ustanovka
echo ===================================================

where docker >nul 2>nul
if %errorlevel%==0 goto DOCKER

echo Docker ne nayden - stavlyu lokalno na Python (oflayn, iz .\vendor)...
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

echo [1/3] Sozdayu virtualnoe okruzhenie...
%PY% -m venv .venv
call .venv\Scripts\activate.bat

echo [2/3] Ustanavlivayu zavisimosti (oflayn)...
pip install -q --no-index --find-links vendor -r requirements.txt

echo [3/3] Gotovo.
echo.
echo  Zapusk:    start.bat
echo  Proverka:  verify.bat
echo  Posle zapuska otkroyte:  http://localhost:8000
goto END

:DOCKER
echo [1/3] Nayden Docker - sobirayu obraz...
docker build -q -t postcontrol-suite .
echo [2/3] Zapuskayu konteyner...
docker rm -f postcontrol >nul 2>nul
docker run -d -p 8000:8000 -v "%cd%/config:/srv/postcontrol/config" -v "%cd%/references:/srv/postcontrol/references" -v "%cd%/storage:/srv/postcontrol/storage" --name postcontrol --restart unless-stopped postcontrol-suite
echo [3/3] Proveryayu...
timeout /t 4 >nul
curl -s http://localhost:8000/api/health
echo.
echo  GOTovo. Otkroyte:  http://localhost:8000
echo  Proverka na testovom zadanii:  verify.bat

:END
pause
