#!/usr/bin/env bash
# Diagnosi delle canoniche Python fallite (scripts/debug_canonical.py) su un nodo defq.
# Si lancia dal nodo di login:
#   bash scripts/debug_canonical.sh humaneval/32 humaneval/139 mbpp/255 2>&1 | tee -a docs/milestone_logs/M4_cluster.txt
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 16 --mem 32G -t 60 \
    --job-name wmb-debug-canonical bash -lc "
        module load apptainer/apptainer.module
        export PATH=$WMB/bench-core/bin:\$PATH
        cd $REPO_ROOT
        echo \"##### diagnosi canoniche Python: nodo \$(hostname)\"
        python scripts/debug_canonical.py $*
    "
