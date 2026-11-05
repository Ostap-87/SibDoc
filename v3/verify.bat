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
    echo ---------------------------------------------------
    echo  DEMO: DT RF / CN dec / SAP - ozhidaetsya "6. ... ISSUE=4"
    python run_cli.py --wide data/demo/wide_table_demo.xlsx --purchase data/demo/purchase_demo.xlsx --ru-dt data/demo/ru_dt/*.xml --cn-dec data/demo/cn_dec_demo.xlsx --sap-vs-cd data/demo/sap_vs_cd_demo.csv
)

echo ---------------------------------------------------
echo  ETALON: price 46 strok: 41 OK / 5 ISSUE; hs_code 33 OK / 18 REVIEW;
echo          incoterms 43 OK / 8 REVIEW; contracts 43 OK / 0 ISSUE / 3 REVIEW;
echo          decl. elements 17 OK / 4 REVIEW; DQ: 1 ERROR
echo  Esli chisla vyshe sovpali - instrument rabotaet.
echo ---------------------------------------------------
pause
