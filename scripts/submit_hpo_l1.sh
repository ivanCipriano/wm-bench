#!/usr/bin/env bash
# Sweep dell'HPO della Milestone 8 su L1 dev, per un metodo (griglia configs/hpo/<metodo>.yaml):
#
#   bash scripts/submit_hpo_l1.sh trajectories promptmark 1/1  # 0) solo PromptMark: traiettorie (ADR-009)
#   bash scripts/submit_hpo_l1.sh watermark stone 1/1          # 1) campioni marcati per configurazione
#   bash scripts/submit_hpo_l1.sh execute   stone 1/1          # 2) Pass@1 dei marcati (defq)
#   bash scripts/submit_hpo_l1.sh detect    stone 1/1          # 3) punteggi di positivi e negativi
#   bash scripts/submit_hpo_l1.sh calibrate stone              # 4) soglie per configurazione
#   bash scripts/submit_hpo_l1.sh tune      stone              # 5) scelta della configurazione
#   aggiungere --dry-run in fondo per vedere solo il piano; K/M divide le celle fra le due persone.
#
# Ordine: 0) → 1) → 2) e 3) insieme → 4) dopo 3) → 5) dopo 2) e 4). Le configurazioni già fatte
# (es. il default della M7) si saltano. Variabile opzionale: JOBS (default: tutti i posti liberi,
# cioè 10 meno i job già in coda o in esecuzione dell'utente; MaxSubmit = 10, cluster_info §4).
set -euo pipefail

USAGE="usage: submit_hpo_l1.sh trajectories|watermark|execute|detect|calibrate|tune METHOD [K/M] [--dry-run]"
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
COMMON=("methods=[$METHOD]" "levels=[L1]" "splits=[dev]" "languages=$LANGS")
SWEEP=("hpo.grid=$METHOD")
if [ "$METHOD" = "promptmark" ]; then
    SWEEP+=("hpo.part=candidates")
fi

case "$STEP" in
    trajectories)
        [ "$METHOD" = "promptmark" ] || { echo "trajectories: only for promptmark" >&2; exit 2; }
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=watermark" "hpo.grid=$METHOD"
              "hpo.part=trajectories" "${COMMON[@]}")
        ;;
    watermark|detect)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=$STEP" "${SWEEP[@]}" "${COMMON[@]}")
        ;;
    execute)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=execute" "${SWEEP[@]}"
              "execution.sources=[llm_watermarked]" "${COMMON[@]}")
        ;;
    calibrate)
        SHARE="1/1"; MODE="${3:-}"
        ARGS=(--jobs 1 "stage=calibrate" "${SWEEP[@]}" "${COMMON[@]}")
        ;;
    tune)
        SHARE="1/1"; MODE="${3:-}"
        ARGS=(--jobs 1 "stage=tune" "${COMMON[@]}")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

echo "##### job attivi: $ACTIVE, posti liberi: $FREE su $MAX_JOBS"
if [ "$MODE" != "--dry-run" ]; then
    NEED=1
    [ "$STEP" = calibrate ] || [ "$STEP" = tune ] || NEED="${JOBS:-1}"
    if [ "$FREE" -lt 1 ] || [ "$NEED" -gt "$FREE" ]; then
        echo "not enough free slots: need $NEED, free $FREE (wait for a job to end or lower JOBS)" >&2
        exit 2
    fi
fi
echo "##### piano (dry run): HPO $STEP $METHOD, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: HPO $STEP $METHOD, quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.34j %.8T %.10M"
