#!/usr/bin/env bash
# Invia l'esecuzione dei test di L1 (Milestone 4) con bench submit, su defq (profilo slurm_cpu).
# Gli argomenti di Hydra sono scritti qui (niente copia-incolla, vedi submit_baseline_l1.sh).
#
# Uso (dalla radice del repository, con bench-core nel PATH):
#   bash scripts/submit_execute_l1.sh groundtruth            # 1) ground truth di EvalPlus (1 job)
#   bash scripts/submit_execute_l1.sh execute 1/1            # 2) canoniche + baseline, tutte le celle
#   bash scripts/submit_execute_l1.sh execute 1/2            #    oppure metà a testa (l'altra: 2/2)
#   aggiungere --dry-run in fondo per vedere solo il piano.
#
# Il passo 2 va lanciato quando il passo 1 è finito (le celle Python leggono la ground truth).
# Variabile opzionale: JOBS (default 2), job paralleli per la propria quota.
set -euo pipefail

WHAT="${1:?usage: submit_execute_l1.sh groundtruth|execute [K/M] [--dry-run]}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002

case "$WHAT" in
    groundtruth)
        SHARE="1/1"; MODE="${2:-}"
        ARGS=(--jobs 1 "stage=evalplus_groundtruth")
        ;;
    execute)
        SHARE="${2:?usage: submit_execute_l1.sh execute K/M [--dry-run]}"; MODE="${3:-}"
        ARGS=(--jobs "${JOBS:-2}" --share "$SHARE" "stage=execute" "levels=[L1]" "splits=[dev,test]")
        ;;
    *) echo "unknown step: $WHAT" >&2; exit 2 ;;
esac

echo "##### piano (dry run): $WHAT, quota $SHARE"
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "$MODE" = "--dry-run" ]; then
    exit 0
fi
echo "##### invio: $WHAT, quota $SHARE"
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.34j %.8T %.10M"
