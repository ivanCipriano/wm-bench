#!/usr/bin/env bash
# Livello 3 (Milestone 13, ADR-016): dati di ClassEval, CodeSearchNet e The Stack C++.
#
#   bash scripts/submit_l3.sh data        # prepare_data L3, 4 linguaggi (CPU, 1 job)
#   aggiungere --dry-run in fondo per vedere solo il piano.
#
# Poi, sul nodo di login: python scripts/preview_l3_prompts.py (un prompt d'esempio per dataset e
# linguaggio, conteggi dei filtri e delle esclusioni D39). Canoniche, baseline ed esecuzione si
# aggiungono dopo l'approvazione dei prompt (TODO 35).
set -euo pipefail

STEP="${1:?usage: submit_l3.sh data [--dry-run]}"
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

case "$STEP" in
    data)
        NEED=1; ARGS=(--jobs 1 "stage=prepare_data" "levels=[L3]")
        ;;
    *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

echo "##### job attivi: $ACTIVE, posti liberi: $FREE su $MAX_JOBS"
if [ "$MODE" != "--dry-run" ] && { [ "$FREE" -lt 1 ] || [ "$NEED" -gt "$FREE" ]; }; then
    echo "not enough free slots: need $NEED, free $FREE (wait for a job to end)" >&2
    exit 2
fi
echo "##### piano (dry run): L3 $STEP"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: L3 $STEP"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -u "$USER" -o "%.10i %.34j %.8T %.10M"
