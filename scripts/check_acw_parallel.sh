#!/usr/bin/env bash
# Verifica della rilevazione di ACW in parallelo (scripts/check_acw_parallel.py) in un job su defq.
# Si lancia dal nodo di login, dopo `source ~/.bashrc` (SOURCERY_TOKEN, mai nei file):
#
#   bash scripts/check_acw_parallel.sh RUN_DIR [--n 100]
#
# Il log va in $WMB/logs/check_acw_parallel_<jobid>.log. Il job è uno script bash di login
# (`bash -l`), come check_attacks_canonical.sh; sbatch gli passa l'ambiente, token compreso.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
if [ -z "${SOURCERY_TOKEN:-}" ]; then
    echo "SOURCERY_TOKEN not set: run 'source ~/.bashrc' first" >&2
    exit 2
fi
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
JOB_SCRIPT="$WMB/logs/check_acw_parallel.sbatch"
cat > "$JOB_SCRIPT" <<SBATCH
#!/bin/bash -l
set -e
export PATH=$WMB/bench-core/bin:\$PATH
cd $REPO_ROOT
umask 002
echo "##### nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
python scripts/check_acw_parallel.py $*
SBATCH
JOB=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
    -c 16 --mem 32G -t 120 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-check-acw-parallel \
    -o "$WMB/logs/check_acw_parallel_%j.log" "$JOB_SCRIPT")
echo "job $JOB submitted; log: $WMB/logs/check_acw_parallel_$JOB.log"
