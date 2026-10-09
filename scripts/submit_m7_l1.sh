#!/usr/bin/env bash
# Milestone 7 su L1 dev, per un metodo con la configurazione di default:
#
#   bash scripts/submit_m7_l1.sh execute   stone 1/1   # 1) Pass@1 dei marcati (e della gemella), defq
#   bash scripts/submit_m7_l1.sh detect    stone 1/1   # 2) punteggi di positivi e negativi (gpuq o defq)
#   bash scripts/submit_m7_l1.sh calibrate stone       # 3) soglie provvisorie sui negativi umani dev
#   bash scripts/submit_m7_l1.sh metrics   stone       # 4) metriche con IC su dev
#   aggiungere --dry-run in fondo per vedere solo il piano; K/M divide le celle fra le due persone.
#
# 1) e 2) sono indipendenti e si possono lanciare insieme; 3) dopo 2); 4) dopo 1) e 3).
# Linguaggi per metodo (regola dei linguaggi, D6, D17, D19, D22): le celle N/A non si inviano.
# Variabile opzionale: JOBS (default 2).
set -euo pipefail

USAGE="usage: submit_m7_l1.sh execute|detect|calibrate|metrics METHOD [K/M] [--dry-run]"
STEP="${1:?$USAGE}"
METHOD="${2:?$USAGE}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002

case "$METHOD" in
    sweet) LANGS="[python,java,cpp,javascript]" ;;
    stone) LANGS="[python,java,cpp]" ;;
    mcgmark|promptmark|acw) LANGS="[python]" ;;
    *) echo "unknown method: $METHOD" >&2; exit 2 ;;
esac
COMMON=("methods=[$METHOD]" "levels=[L1]" "splits=[dev]" "languages=$LANGS")

case "$STEP" in
    execute)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-2}" --share "$SHARE" "stage=execute"
              "execution.sources=[llm_watermarked,baseline_twin]" "${COMMON[@]}")
        ;;
    detect)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-2}" --share "$SHARE" "stage=detect" "${COMMON[@]}")
        ;;
    calibrate|metrics)
        SHARE="1/1"; MODE="${3:-}"
        ARGS=(--jobs 1 "stage=$STEP" "${COMMON[@]}")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

echo "##### piano (dry run): $STEP $METHOD, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: $STEP $METHOD, quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.34j %.8T %.10M"
