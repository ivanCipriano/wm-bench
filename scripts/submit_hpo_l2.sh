#!/usr/bin/env bash
# Metodi su L2 dev (Milestone 9) e scelta definitiva su L1+L2, per un metodo (griglia della M8,
# configs/hpo/<metodo>.yaml, default compreso):
#
#   bash scripts/submit_hpo_l2.sh twin         mcgmark    1/1  # 0a) solo MCGMark: baseline gemella L2 dev
#   bash scripts/submit_hpo_l2.sh trajectories promptmark 1/1  # 0b) solo PromptMark: traiettorie L2 (ADR-009)
#   bash scripts/submit_hpo_l2.sh watermark    stone      1/1  # 1) campioni marcati L2 dev per configurazione
#   bash scripts/submit_hpo_l2.sh execute      stone      1/1  # 2) Pass@1 dei marcati L2 (defq)
#   bash scripts/submit_hpo_l2.sh detect       stone      1/1  # 3) punteggi L2 dev + integrazione L1+L2
#   bash scripts/submit_hpo_l2.sh calibrate    stone           # 4) soglie DEFINITIVE su L1+L2
#   bash scripts/submit_hpo_l2.sh tune         stone           # 5) scelta DEFINITIVA su L1+L2
#   aggiungere --dry-run in fondo per vedere solo il piano; K/M divide le celle fra le due persone.
#   K/M:k/m prende la parte k di m della quota K/M (es. 1/2:1/2 e 1/2:2/2 per spartire il resto
#   della quota 1/2 fra due persone; prima si cancellano i job della quota 1/2 intera).
#
# Ordine: 0) → 1) → 2) e 3) insieme → 4) dopo 3) → 5) dopo 2) e 4). Per 4) e 5) servono anche i
# passi della M8 su L1 (HPO L1 completo del metodo). Le soglie e la scelta definitive vanno in
# percorsi propri (thresholds/.../L1L2/, hpo/selected_L1L2/): quelle provvisorie della M8 restano.
# Stato: python scripts/check_hpo.py METHOD --level L2. Variabile opzionale: JOBS (default: tutti i
# posti liberi, cioè 10 meno i job già in coda o in esecuzione dell'utente; cluster_info §4).
set -euo pipefail

USAGE="usage: submit_hpo_l2.sh twin|trajectories|watermark|execute|detect|calibrate|tune METHOD [K/M] [--dry-run]"
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
L2=("methods=[$METHOD]" "levels=[L2]" "splits=[dev]" "languages=$LANGS")
FINAL=("methods=[$METHOD]" "levels=[L1,L2]" "splits=[dev]" "languages=$LANGS")
SWEEP=("hpo.grid=$METHOD")
if [ "$METHOD" = "promptmark" ]; then
    SWEEP+=("hpo.part=candidates")
fi
SOURCES="[llm_watermarked]"
if [ "$METHOD" = "mcgmark" ]; then
    SOURCES="[llm_watermarked,baseline_twin]"  # gemella: riferimento di ΔPass@1 (D25)
fi

case "$STEP" in
    twin)
        [ "$METHOD" = "mcgmark" ] || { echo "twin: only for mcgmark" >&2; exit 2; }
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-2}" --share "$SHARE" "stage=generate_baseline_twin" "${L2[@]}")
        ;;
    trajectories)
        [ "$METHOD" = "promptmark" ] || { echo "trajectories: only for promptmark" >&2; exit 2; }
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=watermark" "hpo.grid=$METHOD"
              "hpo.part=trajectories" "${L2[@]}")
        ;;
    watermark|detect)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=$STEP" "${SWEEP[@]}" "${L2[@]}")
        ;;
    execute)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=execute" "${SWEEP[@]}"
              "execution.sources=$SOURCES" "${L2[@]}")
        ;;
    calibrate)
        SHARE="1/1"; MODE="${3:-}"
        ARGS=(--jobs 1 "stage=calibrate" "${SWEEP[@]}" "${FINAL[@]}")
        ;;
    tune)
        SHARE="1/1"; MODE="${3:-}"
        ARGS=(--jobs 1 "stage=tune" "${FINAL[@]}")
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
echo "##### piano (dry run): L2 $STEP $METHOD, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: L2 $STEP $METHOD, quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.50j %.8T %.10M"
