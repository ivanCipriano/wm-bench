#!/usr/bin/env bash
# Milestone 12: impercettibilità sui campioni marcati di L1 test di un metodo CONGELATO (ADR-015):
#
#   bash scripts/submit_m12.sh perplexity stone 1/1   # 1) NLL con StarCoder2-7B (gpuq): marcati,
#                                                     #    baseline (comune ai metodi) e gemella
#   bash scripts/submit_m12.sh logreg     stone 1/1   # 2) classificatore logreg (CPU, defq)
#   bash scripts/submit_m12.sh unixcoder  stone 1/1   # 3) classificatore UniXcoder (gpuq)
#   bash scripts/submit_m12.sh metrics    stone       # 4) CodeBLEU, ΔNLL/ΔPPL, AUROC (CPU, defq)
#   aggiungere --dry-run in fondo per vedere solo il piano; K/M divide le celle fra le due persone.
#
# Ordine: 1), 2) e 3) insieme; 4) dopo tutti e tre (AFTER=perplexity,logreg,unixcoder).
# Stato e report: python scripts/check_m12.py METODO [--report].
# AFTER (opzionale): passi da attendere, come in submit_attacks_l1.sh (ID salvati a ogni invio,
# un file per utente).
set -euo pipefail
USAGE="usage: submit_m12.sh perplexity|logreg|unixcoder|metrics METHOD [K/M] [--dry-run]"
STEP="${1:?$USAGE}"
METHOD="${2:?$USAGE}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"

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
TEST=("methods=[$METHOD]" "levels=[L1]" "splits=[test]" "languages=$LANGS" "attacks=[]")

case "$STEP" in
    perplexity)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=perplexity"
              "execution.sources=[llm_baseline,llm_watermarked,baseline_twin]" "${TEST[@]}")
        ;;
    logreg|unixcoder)
        SHARE="${3:?$USAGE}"; MODE="${4:-}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" --share "$SHARE" "stage=adversarial_$STEP"
              "${TEST[@]}")
        ;;
    metrics)
        SHARE="1/1"; MODE="${3:-}"
        ARGS=(--jobs 1 "stage=metrics_imperceptibility" "${TEST[@]}")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

IDS_DIR="$WMB/logs/submit_ids"
mkdir -p "$IDS_DIR"
ids_file() { echo "m12_${1}_${METHOD}_${USER}.txt"; }
AFTER_ARGS=()
if [ -n "${AFTER:-}" ]; then
    QUEUED=" $(squeue -u "$USER" -h -o %i | tr '\n' ' ') "
    WAIT=()
    for step in ${AFTER//,/ }; do
        [ -f "$IDS_DIR/$(ids_file "$step")" ] || { echo "AFTER: no saved jobs for $step" >&2; exit 2; }
        for id in $(cat "$IDS_DIR/$(ids_file "$step")"); do
            case "$QUEUED" in *" $id "*) WAIT+=("$id") ;; esac
        done
    done
    if [ "${#WAIT[@]}" -gt 0 ]; then
        AFTER_ARGS=(--after "$(IFS=:; echo "${WAIT[*]}")")
        echo "##### parte dopo i job ${WAIT[*]} ($AFTER)"
    else
        echo "##### AFTER=$AFTER: nessun job ancora in coda, parte subito"
    fi
fi
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
OUT=$(bench submit "${AFTER_ARGS[@]}" "${ARGS[@]}")
echo "$OUT" | grep -v "parameters:"
echo "$OUT" | sed -n 's/^SLURM job \([0-9][0-9]*\) .*/\1/p' > "$IDS_DIR/$(ids_file "$STEP")"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.50j %.8T %.10M"
