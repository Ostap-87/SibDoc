#!/usr/bin/env bash
# ============================================================
#  PostControl Suite — проверка на тестовом задании.
#  Прогоняет эталонные данные и сверяет числа с эталоном,
#  подтверждённым ручной методикой (docs/SPECIALIST_GUIDE.md §8).
# ============================================================
cd "$(dirname "$0")"
LOG=/tmp/pcs_verify.log

echo "==================================================="
echo " PostControl Suite — проверка работоспособности"
echo "==================================================="

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^postcontrol$'; then
    docker exec postcontrol python run_cli.py \
        --wide data/wide_table.xlsx --purchase data/purchase.xlsx | tee "$LOG"
else
    [ -f .venv/bin/activate ] && . .venv/bin/activate
    python run_cli.py --wide data/wide_table.xlsx --purchase data/purchase.xlsx | tee "$LOG"
fi

echo "---------------------------------------------------"
PASS=1
check() { grep -q "$1" "$LOG" && echo "  OK   $2" || { echo "  FAIL $2"; PASS=0; }; }

check "1. Price           total=37   OK=32   ISSUE=5"  "цены: 37 строк, 32 OK, 5 ISSUE"
check "2. HS code         total=55   OK=33"            "HS-коды: 55 строк, 33 OK, 22 REVIEW"
check "3. Incoterms       total=55   OK=47"            "инкотермсы: 47 OK, 8 REVIEW"
check "5. Contract        total=37   OK=32   ISSUE=2"  "контракты: 2 ISSUE"
check "DQ-ERROR: 1"                                     "DQ-детекция: 1 ERROR (extra_costs_value)"
check "post_control_report.xlsx"                        "Excel-отчёт сформирован"
check "post_control_report.pdf"                         "PDF-отчёт сформирован"

echo "---------------------------------------------------"
if [ "$PASS" = "1" ]; then
    echo " РЕЗУЛЬТАТ: ВСЁ СОВПАЛО — инструмент работает корректно."
else
    echo " РЕЗУЛЬТАТ: ЕСТЬ РАСХОЖДЕНИЯ — проверьте установку и данные."
fi
