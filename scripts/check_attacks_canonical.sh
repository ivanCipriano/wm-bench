#!/usr/bin/env bash
# Verifica degli attacchi T1 sulle canoniche di L1 (scripts/check_attacks_canonical.py, ADR-012)
# in un job su defq (CPU + Apptainer). Si lancia dal nodo di login, DOPO setup_tools.sh e la fase
# attack_pools:
#
#   bash scripts/check_attacks_canonical.sh                 # tutti i linguaggi e gli attacchi T1
#   bash scripts/check_attacks_canonical.sh --languages java --attacks T1.2
#
# Il log va in $WMB/logs/check_attacks_canonical_<jobid>.log; a job finito si copia in
# docs/milestone_logs/M10_canonical_attacks.txt (comando stampato qui sotto).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
ARGS="$*"
JOB=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
    -c 16 --mem 64G -t 420 "${EXCLUDE[@]}" --job-name wmb-check-attacks-canonical \
    -o "$WMB/logs/check_attacks_canonical_%j.log" --wrap "
        set -e
        module load apptainer/apptainer.module
        export PATH=$WMB/bench-core/bin:\$PATH
        cd $REPO_ROOT
        umask 002
        echo \"##### nodo \$(hostname), commit \$(git rev-parse --short HEAD)\"
        python scripts/check_attacks_canonical.py $ARGS
    ")
echo "job $JOB submitted; log: $WMB/logs/check_attacks_canonical_$JOB.log"
echo "a job finito: cp $WMB/logs/check_attacks_canonical_$JOB.log docs/milestone_logs/M10_canonical_attacks.txt"
