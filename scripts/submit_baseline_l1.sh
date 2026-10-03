#!/usr/bin/env bash
# Invia la baseline L1 (generate_baseline, dev+test, entrambi i modelli) con bench submit.
# Gli argomenti di Hydra sono scritti qui, così non si copiano a mano (spazi non separabili).
#
# Uso (dalla radice del repository, con bench-core nel PATH):
#   bash scripts/submit_baseline_l1.sh 1/2          # prima metà delle celle, 3 job
#   bash scripts/submit_baseline_l1.sh 2/2          # seconda metà (l'altra persona)
#   bash scripts/submit_baseline_l1.sh 1/1          # tutte le celle
#   bash scripts/submit_baseline_l1.sh 1/2 --dry-run   # solo il piano, nessun invio
#
# Variabile opzionale: JOBS (default 3), numero di job paralleli per la propria quota.
set -euo pipefail

SHARE="${1:?usage: submit_baseline_l1.sh K/M [--dry-run]}"
MODE="${2:-}"
JOBS="${JOBS:-3}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002

ARGS=(--jobs "$JOBS" --share "$SHARE" "stage=generate_baseline" "levels=[L1]" "splits=[dev,test]")

echo "##### piano (dry run): quota $SHARE, $JOBS job"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.34j %.8T %.10M"
