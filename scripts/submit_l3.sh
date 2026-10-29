#!/usr/bin/env bash
# Livello 3 (Milestone 13, ADR-016): ClassEval, CodeSearchNet e The Stack C++, solo test.
#
#   bash scripts/submit_l3.sh data        # 1) prepare_data L3, 4 linguaggi (CPU, 1 job);
#                                         #    FORCE=1 per rifarlo sopra i dati esistenti
#   bash scripts/submit_l3.sh canonical   # 2) canoniche L3, 3 ripetizioni (CPU, 1 job)
#   bash scripts/submit_l3.sh baseline    # 3) baseline L3, 2 modelli x 4 linguaggi (GPU; ClassEval
#                                         #    a 2.048 token, D40)
#   bash scripts/submit_l3.sh execute     # 4) esecuzione della baseline L3 (CPU), dopo 3)
#   aggiungere --dry-run in fondo per vedere solo il piano.
#
# Esecuzione con l'immagine sandbox_l3 (bash scripts/build_sandbox.sh l3): test di ClassEval;
# CodeSearchNet e The Stack: validità con tree-sitter (riferimento) e compilazione (informativa).
# Anteprima dei prompt: python scripts/preview_l3_prompts.py. Variabile opzionale: JOBS.
set -euo pipefail

STEP="${1:?usage: submit_l3.sh data|canonical|baseline|execute [--dry-run]}"
MODE="${2:-}"
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
LEVEL=("levels=[L3]" "splits=[test]")
IMAGE=("execution.image_sif=$WMB/containers/sandbox_l3.sif"
       "execution.image_dir=$WMB/containers/sandbox_l3_dir")
case "$STEP" in
    data)
        NEED=1; ARGS=(--jobs 1 "stage=prepare_data" "levels=[L3]")
        if [ "${FORCE:-0}" = "1" ]; then ARGS+=("force=true"); fi
        ;;
    canonical)
        NEED=1
        ARGS=(--jobs 1 "stage=execute" "${LEVEL[@]}" "${IMAGE[@]}" "execution.sources=[canonical]")
        ;;
    baseline)
        NEED="${JOBS:-1}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" "stage=generate_baseline" "${LEVEL[@]}")
        ;;
    execute)
        NEED="${JOBS:-1}"
        ARGS=(--jobs "${JOBS:-2}" "stage=execute" "${LEVEL[@]}" "${IMAGE[@]}"
              "execution.sources=[llm_baseline]")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

echo "##### job attivi: $ACTIVE, posti liberi: $FREE su $MAX_JOBS"
if [ "$MODE" != "--dry-run" ] && { [ "$FREE" -lt 1 ] || [ "$NEED" -gt "$FREE" ]; }; then
    echo "not enough free slots: need $NEED, free $FREE (wait for a job to end or lower JOBS)" >&2
    exit 2
fi
if [ "$STEP" = canonical ] || [ "$STEP" = execute ]; then
    if [ ! -f "$WMB/containers/sandbox_l3.sif.sha256" ]; then
        echo "L3 sandbox image missing: run bash scripts/build_sandbox.sh l3" >&2
        exit 2
    fi
fi
echo "##### piano (dry run): L3 $STEP"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: L3 $STEP"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -u "$USER" -o "%.10i %.34j %.8T %.10M"
