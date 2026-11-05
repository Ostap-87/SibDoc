@echo off
REM Zapusk PostControl Suite (posle install.bat)
cd /d "%~dp0"

docker ps --format "{{.Names}}" 2>nul | findstr /x "postcontrol" >nul
if %errorlevel%==0 (
    echo Konteyner uzhe rabotaet: http://localhost:8000
    pause & exit /b 0
)
docker ps -a --format "{{.Names}}" 2>nul | findstr /x "postcontrol" >nul
if %errorlevel%==0 (
    docker start postcontrol >nul
    echo Konteyner zapushchen: http://localhost:8000
    pause & exit /b 0
)

if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
echo Start API+UI na http://localhost:8000  (Ctrl+C - ostanovit)
uvicorn app.server:app --host 0.0.0.0 --port 8000
pause
