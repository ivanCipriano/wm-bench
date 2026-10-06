#!/usr/bin/env bash
# Invia la baseline gemella di L1 (dev) per un metodo con twin_baseline (MCGMark, D20), solo Python (gpuq).
#
#   bash scripts/submit_baseline_twin_l1.sh mcgmark 1/1            # entrambi i modelli
#   bash scripts/submit_baseline_twin_l1.sh mcgmark 1/2            # metà a testa (l'altra persona: 2/2)
#   bash scripts/submit_baseline_twin_l1.sh mcgmark 1/1 --dry-run  # solo il piano
# Variabile opzionale: JOBS (default 2).
set -euo pipefail

METHOD="${1:?usage: submit_baseline_twin_l1.sh METHOD K/M [--dry-run]}"
SHARE="${2:?usage: submit_baseline_twin_l1.sh METHOD K/M [--dry-run]}"
MODE="${3:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002

ARGS=(--jobs "${JOBS:-2}" --share "$SHARE" "stage=generate_baseline_twin" "methods=[$METHOD]"
      "levels=[L1]" "languages=[python]" "splits=[dev]")
echo "##### piano (dry run): baseline gemella $METHOD, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: baseline gemella $METHOD, quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.34j %.8T %.10M"
