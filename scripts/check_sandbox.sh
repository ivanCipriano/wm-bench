#!/usr/bin/env bash
# Verifica della sandbox su un nodo di calcolo defq (cluster_info §5: le prove su --fakeroot e
# --network none erano state fatte solo sul nodo di login). Si lancia dal nodo di login:
#
#   bash scripts/check_sandbox.sh
#
# Chiede con srun un nodo defq (4 CPU, 30 minuti), carica Apptainer e lancia i test con marker
# apptainer (rete assente, isolamento, versioni, canoniche HumanEvalPack) e il doctor.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"

srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 4 --mem 16G -t 30 \
    --job-name wmb-check-sandbox bash -lc "
        set -uo pipefail
        umask 002
        module load apptainer/apptainer.module
        export PATH=$WMB/bench-core/bin:\$PATH
        cd $REPO_ROOT
        echo \"node: \$(hostname)\"
        apptainer --version
        WMB_REQUIRE_DATA=1 python -m pytest -q -rs -m apptainer tests/integration/test_sandbox_apptainer.py
        bench doctor > /tmp/wmb_doctor_\$\$.txt; grep -F -A5 '[apptainer]' /tmp/wmb_doctor_\$\$.txt; tail -n 1 /tmp/wmb_doctor_\$\$.txt; rm -f /tmp/wmb_doctor_\$\$.txt
    "
