#!/usr/bin/env bash
# ============================================================
#  PostControl Suite — проверка работоспособности.
#  1) Эталонные данные: числа сверяются с эталоном (docs/SPECIALIST_GUIDE.md §8).
#  2) Демо-набор (вымышленные ДТ РФ, 报关单, SAP): сквозная сверка находит
#     все заложенные расхождения.
# ============================================================
cd "$(dirname "$0")"
LOG=$(mktemp -t pcs_verify.XXXXXX)
LOG2=$(mktemp -t pcs_verify_demo.XXXXXX)

echo "==================================================="
echo " PostControl Suite — проверка работоспособности"
echo "==================================================="

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^postcontrol$'; then
    RUN="docker exec postcontrol python run_cli.py"
else
    [ -f .venv/bin/activate ] && . .venv/bin/activate
    PY=$(command -v python3 || command -v python)
    RUN="$PY run_cli.py"
fi

$RUN --wide data/wide_table.xlsx --purchase data/purchase.xlsx | tee "$LOG"
echo "---------------------------------------------------"
$RUN --wide data/demo/wide_table_demo.xlsx --purchase data/demo/purchase_demo.xlsx \
     --ru-dt "data/demo/ru_dt/*.xml" --cn-dec data/demo/cn_dec_demo.xlsx \
     --sap-vs-cd data/demo/sap_vs_cd_demo.csv > "$LOG2"
grep -E "total=|курс:" "$LOG2"

echo "---------------------------------------------------"
PASS=1
check() { grep -qE "$2" "$1" && echo "  OK   $3" || { echo "  FAIL $3"; PASS=0; }; }

check "$LOG"  "1\. Price +total=46 +OK=41 +ISSUE=5 "        "цены: 46 товарных строк, 41 OK, 5 ISSUE (те же 5 контейнеров)"
check "$LOG"  "2\. HS code +total=51 +OK=33 +ISSUE=0 +REVIEW=18" "HS-коды: 33 OK, 18 REVIEW"
check "$LOG"  "3\. Incoterms +total=51 +OK=43 +ISSUE=0 +REVIEW=8" "условия поставки: 43 OK, 8 REVIEW"
check "$LOG"  "4\. Decl\. elements +total=21 +OK=17 +ISSUE=0 +REVIEW=4" "элементы декларации: 17 OK, 4 REVIEW"
check "$LOG"  "5\. Contract +total=46 +OK=43 +ISSUE=0 +REVIEW=3" "контракты: склеенные засчитаны, 3 REVIEW"
check "$LOG"  "DQ-ERROR: 1"                                    "качество данных: 1 ERROR (extra_costs_value)"
check "$LOG"  "post_control_report\.xlsx"                      "Excel-отчёт сформирован"
check "$LOG"  "post_control_report\.pdf"                       "PDF-отчёт сформирован"
check "$LOG2" "0\. Completeness +total=5 +OK=4 +ISSUE=1"       "демо: полнота — 1 декларации нет в ручной таблице"
check "$LOG2" "1\. Price +total=7 +OK=5 +ISSUE=1 +REVIEW=1"    "демо: цена — 1 расхождение, 1 совпадение через курс (или «нет курса»)"
check "$LOG2" "6\. .* +total=11 +OK=6 +ISSUE=4 +REVIEW=1"      "демо: ДТ ↔ 报关单 ↔ SAP — 4 расхождения (цена, вес, код, ДТ нет в SAP)"

rm -f "$LOG" "$LOG2"
echo "---------------------------------------------------"
if [ "$PASS" = "1" ]; then
    echo " РЕЗУЛЬТАТ: ВСЁ СОВПАЛО — инструмент работает корректно."
else
    echo " РЕЗУЛЬТАТ: ЕСТЬ РАСХОЖДЕНИЯ — проверьте установку и данные."
    exit 1
fi
