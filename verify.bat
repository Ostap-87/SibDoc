@echo off
REM ============================================================
REM  Proverka na testovom zadanii: etalonnyy progon.
REM  Chisla dolzhny sovpast s etalonom (docs/SPECIALIST_GUIDE.md §8).
REM ============================================================
cd /d "%~dp0"
echo ===================================================
echo  PostControl Suite - proverka rabotosposobnosti
echo ===================================================

docker ps --format "{{.Names}}" 2>nul | findstr /x "postcontrol" >nul
if %errorlevel%==0 (
    docker exec postcontrol python run_cli.py --wide data/wide_table.xlsx --purchase data/purchase.xlsx
) else (
    if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
    python run_cli.py --wide data/wide_table.xlsx --purchase data/purchase.xlsx
)

echo ---------------------------------------------------
echo  ETALON: price 32 OK / 5 ISSUE; hs_code 22 REVIEW;
echo          contracts 2 ISSUE; DQ: 1 ERROR
echo  Esli chisla vyshe sovpali - instrument rabotaet.
echo ---------------------------------------------------
pause
