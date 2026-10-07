#!/usr/bin/env bash
# Lista di frequenza di PromptMark (fase promptmark_freq, D8) su defq: un job CPU.
#
#   bash scripts/submit_promptmark_freq.sh 2>&1 | tee -a docs/milestone_logs/M6_cluster.txt
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002

echo "##### invio: promptmark_freq"
bench submit --jobs 1 "stage=promptmark_freq" | grep -v "parameters:"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.9P %.34j %.8T %.10M"
