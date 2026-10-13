#!/usr/bin/env bash
# Livello 2 (CodeNet, Milestone 9): dati, canoniche, baseline ed esecuzione della baseline.
#
#   bash scripts/submit_l2.sh data                # 1) prepare_data L2 + integrazione dev L1+L2 (CPU, 1 job)
#   bash scripts/submit_l2.sh canonical           # 2) esecuzione delle canoniche L2 (CPU, 1 job)
#   bash scripts/submit_l2.sh baseline            # 3) baseline L2, dev+test, 2 modelli (GPU)
#   bash scripts/submit_l2.sh execute             # 4) esecuzione della baseline L2 (CPU)
#   aggiungere --dry-run in fondo per vedere solo il piano.
#
# Ordine: 1) → 2) e 3) insieme → 4) dopo 3). Stato: python scripts/check_l2.py
# Variabile opzionale: JOBS (default: tutti i posti liberi, 10 meno i job dell'utente in squeue).
set -euo pipefail

STEP="${1:?usage: submit_l2.sh data|canonical|baseline|execute [--dry-run]}"
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

LEVEL=("levels=[L2]" "splits=[dev,test]")
case "$STEP" in
    data)
        NEED=1; ARGS=(--jobs 1 "stage=prepare_data" "levels=[L2]")
        ;;
    canonical)
        NEED=1; ARGS=(--jobs 1 "stage=execute" "${LEVEL[@]}" "execution.sources=[canonical]")
        ;;
    baseline)
        NEED="${JOBS:-1}"
        ARGS=(--jobs "${JOBS:-$DEFAULT_JOBS}" "stage=generate_baseline" "${LEVEL[@]}")
        ;;
    execute)
        NEED="${JOBS:-1}"
        ARGS=(--jobs "${JOBS:-2}" "stage=execute" "${LEVEL[@]}" "execution.sources=[llm_baseline]")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

echo "##### job attivi: $ACTIVE, posti liberi: $FREE su $MAX_JOBS"
if [ "$MODE" != "--dry-run" ] && { [ "$FREE" -lt 1 ] || [ "$NEED" -gt "$FREE" ]; }; then
    echo "not enough free slots: need $NEED, free $FREE (wait for a job to end or lower JOBS)" >&2
    exit 2
fi
echo "##### piano (dry run): L2 $STEP"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: L2 $STEP"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -u "$USER" -o "%.10i %.34j %.8T %.10M"
