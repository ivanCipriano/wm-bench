#!/usr/bin/env bash
# Funzione d'ingresso rinominata in T3.1 e T2.2(a) con i token verdi di STONE
# (scripts/t3_1_renames.py --greens) in un job su gpuq: la green list di STONE si genera sulla GPU,
# come nella rilevazione. Si lancia dal nodo di login; circa 10-20 minuti:
#
#   LOG=M11_t3_1_renames.txt bash scripts/t3_1_renames.sh
#
# LOG (obbligatorio) è il nome di un file NUOVO in docs/milestone_logs/: lo script si ferma se
# esiste già. Il log del job va in $WMB/logs/t3_1_renames_<jobid>.log; a job finito si copia con il
# comando stampato qui sotto (cp -n). Il job è uno script bash di login (`bash -l`).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
mkdir -p "$WMB/logs"
[ -n "${LOG:-}" ] || { echo "LOG=<new file name> is required (docs/milestone_logs/)" >&2; exit 2; }
DEST="docs/milestone_logs/$LOG"
[ ! -e "$REPO_ROOT/$DEST" ] || { echo "$DEST exists: choose a new LOG" >&2; exit 2; }
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
JOB_SCRIPT="$WMB/logs/t3_1_renames.sbatch"
cat > "$JOB_SCRIPT" <<EOF
#!/bin/bash -l
set -e
export PATH=$WMB/bench-core/bin:\$PATH
cd $REPO_ROOT
umask 002
echo "##### nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
python scripts/t3_1_renames.py --greens $*
EOF
JOB=$(sbatch --parsable -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos \
    --gres gpu:1 -c 4 --mem 32G -t 60 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-t3-1-renames \
    -o "$WMB/logs/t3_1_renames_%j.log" "$JOB_SCRIPT")
echo "job $JOB submitted; log: $WMB/logs/t3_1_renames_$JOB.log"
echo "a job finito: cp -n $WMB/logs/t3_1_renames_$JOB.log $DEST"
