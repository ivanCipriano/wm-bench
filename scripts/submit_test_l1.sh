#!/usr/bin/env bash
# Fase 0 della Milestone 10: un metodo CONGELATO su L1 test, con la configurazione definitiva
# (freeze/<metodo>.json, ADR-013) e le soglie definitive su L1+L2:
#
#   bash scripts/submit_test_l1.sh twin      mcgmark 1/1  # 0) solo MCGMark: baseline gemella del test
#   bash scripts/submit_test_l1.sh watermark stone   1/1  # 1) campioni marcati di L1 test
#   bash scripts/submit_test_l1.sh execute   stone   1/1  # 2) Pass@1 dei marcati (defq)
#   bash scripts/submit_test_l1.sh detect    stone   1/1  # 3) positivi, negativi umani e LLM del test
#   bash scripts/submit_test_l1.sh metrics   stone        # 4) metriche di L1 test (soglie L1+L2)
#   aggiungere --dry-run in fondo per vedere solo il piano; K/M divide le celle fra le due persone.
#
# Prima: python scripts/freeze_method.py METODO (senza congelamento la pianificazione si ferma).
# Ordine: 0) → 1) → 2) e 3) insieme → 4) dopo 2) e 3). Stato: python scripts/check_test.py METODO.
# Variabile opzionale: JOBS (default: tutti i posti liberi, cioè 10 meno i job già in coda o in
# esecuzione dell'utente; cluster_info §4). Nodi guasti: export WMB_EXCLUDE_NODES=gnode13,gnode14.
set -euo pipefail

USAGE="usage: submit_test_l1.sh twin|watermark|execute|detect|metrics METHOD [K/M] [--dry-run]"
STEP="${1:?$USAGE}"
METHOD="${2:?$USAGE}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"  # bench-core anche in una shell nuova

# Posti liberi: MaxSubmit = 10 job per utente (in coda + in esecuzione), cluster_info §4.
MAX_JOBS=10
ACTIVE=$(squeue -u "$USER" -h 2>/dev/null | wc -l)
FREE=$((MAX_JOBS - ACTIVE))
DEFAULT_JOBS=$(( FREE > 0 ? FREE : 1 ))

case "$METHOD" in
    sweet) LANGS="[python,java,cpp,javascript]" ;;
    stone) LANGS="[python,java,cpp]" ;;
    mcgmark|promptmark|acw) LANGS="[python]" ;;
    *) echo "unknown method: $METHOD" >&2; exit 2 ;;
esac
TEST=("methods=[$METHOD]" "levels=[L1]" "splits=[test]" "languages=$LANGS")
SOURCES="[llm_watermarked]"
if [ "$METHOD" = "mcgmark" ]; then
    SOURCES="[llm_watermarked,baseline_twin]"  # gemella: riferimento di ΔPass@1 (D25)
fi

case "$STEP" in
    twin)
        [ "$METHOD" = "mcgmark" ] || { echo "twin: only for mcgmark" >&2; exit 2; }
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-2}" --share "$SHARE" "stage=generate_baseline_twin" "${TEST[@]}")
        ;;
    watermark|detect)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=$STEP" "${TEST[@]}")
        ;;
    execute)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=execute"
              "execution.sources=$SOURCES" "${TEST[@]}")
        ;;
    metrics)
        SHARE="1/1"; MODE="${3:-}"
        ARGS=(--jobs 1 "stage=metrics" "detection.threshold_levels=[L1,L2]" "${TEST[@]}")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

echo "##### job attivi: $ACTIVE, posti liberi: $FREE su $MAX_JOBS"
if [ "$MODE" != "--dry-run" ]; then
    NEED=1
    [ "$STEP" = metrics ] || NEED="${JOBS:-1}"
    if [ "$FREE" -lt 1 ] || [ "$NEED" -gt "$FREE" ]; then
        echo "not enough free slots: need $NEED, free $FREE (wait for a job to end or lower JOBS)" >&2
        exit 2
    fi
fi
echo "##### piano (dry run): L1 test $STEP $METHOD, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: L1 test $STEP $METHOD, quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.50j %.8T %.10M"
