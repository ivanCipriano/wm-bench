#!/usr/bin/env bash
# Verifica della ricostruzione della griglia di PromptMark (ADR-009) su L2, con il ciclo di
# correttezza spento (D27). Un solo job GPU su gpuq, circa 30-60 min:
#
#   sbatch scripts/m9_promptmark_replay_l2.sh
#
# Meglio lanciarlo dopo `submit_hpo_l2.sh trajectories promptmark` (sceglie i problemi con il
# p-value più basso, per mettere alla prova anche lo stop), ma funziona anche prima.
# Log: docs/milestone_logs/$OUT (default M9_promptmark_replay_l2_2.txt; il job si ferma se esiste
# già, per non riscrivere un log committato: OUT=<nome nuovo> sbatch ...); log del job: $WMB/tmp/promptmark_replay_L2/job_<id>.out
#SBATCH --partition=gpuq
#SBATCH --account=did_tesi_nlp_330
#SBATCH --qos=did_tesi_nlp_330_gpuq_qos
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
#SBATCH --time=02:00:00
#SBATCH --job-name=wmb-m9-pm-replay
#SBATCH --output=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/tmp/promptmark_replay_L2/job_%j.out
set -euo pipefail
umask 002

WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
REPO=/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench
export PATH="$WMB/bench-core/bin:$PATH"
cd "$REPO"
LOG="docs/milestone_logs/${OUT:-M9_promptmark_replay_l2_2.txt}"
[ ! -e "$LOG" ] || { echo "$LOG exists: choose a new OUT" >&2; exit 2; }
python scripts/verify_promptmark_replay.py --level L2 | tee "$LOG"
