@echo off
REM ============================================================
REM  PostControl Suite - ustanovka i zapusk (Windows)
REM  SibDoc3: zakrytyy kontur - BEZ interneta iz komplekta (docs\OFFLINE.md):
REM    install.bat --offline wheelhouse     (papka s bibliotekami *.whl)
REM  Papka .\wheelhouse (ili staraya .\vendor) nahoditsya sama.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo ===================================================
echo  PostControl Suite - ustanovka
echo ===================================================

REM ---- SibDoc3: zakrytyy kontur - parametry
set "OFFDIR=%SIBDOC_OFFLINE_DIR%"
if /I "%~1"=="--offline" (
  if "%~2"=="" (set "OFFDIR=wheelhouse") else (set "OFFDIR=%~2")
)
if "%OFFDIR%"=="" if exist wheelhouse\ set "OFFDIR=wheelhouse"
if "%OFFDIR%"=="" if exist vendor\ set "OFFDIR=vendor"
if not "%OFFDIR%"=="" (
  set PIP_NO_INDEX=1
  set PIP_DISABLE_PIP_VERSION_CHECK=1
  echo  Zakrytyy kontur: biblioteki iz %OFFDIR%, internet ne nuzhen
)

where docker >nul 2>nul
if errorlevel 1 goto PYTHON
docker info >nul 2>nul
if errorlevel 1 goto PYTHON
if "%OFFDIR%"=="" goto DOCKER
REM SibDoc3: zakrytyy kontur - obraz iz komplekta (docker load), bez sborki
if exist offline\docker\postcontrol-suite.tar.gz (
  echo [1/3] Zagruzhayu obraz Docker iz komplekta...
  docker load -i offline\docker\postcontrol-suite.tar.gz
)
docker image inspect postcontrol-suite >nul 2>nul
if errorlevel 1 goto PYTHON
goto DOCKER_RUN

:PYTHON
echo Stavlyu lokalno na Python...
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

echo [1/3] Sozdayu virtualnoe okruzhenie...
%PY% -m venv .venv
if errorlevel 1 (
  echo OSHIBKA: net Python 3.10+. V komplekte - offline\python\python-*-amd64.exe
  goto END
)
call .venv\Scripts\activate.bat

if "%OFFDIR%"=="" (
  echo [2/3] Ustanavlivayu zavisimosti iz interneta...
  pip install -q -r requirements.txt
) else (
  echo [2/3] Ustanavlivayu zavisimosti bez interneta iz %OFFDIR%...
  set "LINKS=--find-links "%OFFDIR%""
  for /d %%D in ("%OFFDIR%\*") do set "LINKS=!LINKS! --find-links "%%~fD""
  pip install -q --no-index !LINKS! -r requirements.txt
)

echo [3/3] Gotovo.
echo.
echo  Zapusk:    start.bat
echo  Proverka:  verify.bat
echo  Posle zapuska otkroyte:  http://localhost:8000
goto END

:DOCKER
echo [1/3] Nayden Docker - sobirayu obraz...
docker build -q -t postcontrol-suite .

:DOCKER_RUN
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
