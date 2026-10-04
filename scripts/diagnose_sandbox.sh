#!/usr/bin/env bash
# Diagnosi dei blocchi di apptainer exec su un nodo defq (Milestone 4, ADR-001).
# In check_sandbox.sh alcune invocazioni non terminavano entro il timeout (anche un semplice
# python3 -c), altre sì. Qui si misura il tempo di ogni variante del comando, aggiungendo
# un'opzione alla volta, 5 volte ciascuna, con un tetto di 90 s per invocazione.
# Si lancia dal nodo di login:
#
#   bash scripts/diagnose_sandbox.sh 2>&1 | tee -a docs/milestone_logs/M4_cluster.txt
set -uo pipefail

WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"

srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 4 --mem 16G -t 60 \
    --job-name wmb-diag-sandbox bash -l <<EOF
set -uo pipefail
module load apptainer/apptainer.module
IMG=$WMB/containers/sandbox_dir
BEEGFS=$WMB/tmp/diag_\$SLURM_JOB_ID
LOCAL=\${TMPDIR:-/tmp}/wmb_diag_\$SLURM_JOB_ID
mkdir -p \$BEEGFS \$LOCAL
echo "##### diagnosi sandbox: nodo \$(hostname), job \$SLURM_JOB_ID, TMPDIR=\${TMPDIR:-unset}"
apptainer --version
df -hT \$LOCAL | tail -1
echo "--- /etc/resolv.conf dell'host"; cat /etc/resolv.conf

BASE="apptainer exec --containall --cleanenv --no-home"
probe() {   # probe <nome> <comando...>
    local name=\$1; shift
    local times=""
    for i in 1 2 3 4 5; do
        local t0=\$(date +%s.%N)
        timeout -k 5 90 "\$@" < /dev/null > /tmp/diag_out_\$\$ 2>&1
        local rc=\$?
        local dt=\$(echo "\$(date +%s.%N) - \$t0" | bc)
        times="\$times \$(printf '%.1f' \$dt)s(rc=\$rc)"
    done
    echo "\$name:\$times"
    tail -n 3 /tmp/diag_out_\$\$ | sed 's/^/    | /'
}

echo "##### tempi (5 prove ciascuna; rc=124 = bloccata e uccisa dopo 90 s)"
probe "A plain           " apptainer exec \$IMG true
probe "B containall      " \$BASE \$IMG true
probe "C +no-mount       " \$BASE --no-mount bind-paths \$IMG true
probe "D +network none   " \$BASE --no-mount bind-paths --net --network none \$IMG true
probe "E +bind beegfs    " \$BASE --no-mount bind-paths --net --network none --pwd /work --bind \$BEEGFS:/work:rw \$IMG true
probe "F +bind local tmp " \$BASE --no-mount bind-paths --net --network none --pwd /work --bind \$LOCAL:/work:rw \$IMG true
probe "G E + timeout py  " \$BASE --no-mount bind-paths --net --network none --pwd /work --bind \$BEEGFS:/work:rw \$IMG timeout --kill-after=5 60 python3 -c "print('ok')"
probe "H D + python      " \$BASE --no-mount bind-paths --net --network none \$IMG python3 -c "print('ok')"
probe "I C + python      " \$BASE --no-mount bind-paths \$IMG python3 -c "print('ok')"
probe "J dns no network  " \$BASE --no-mount bind-paths --net --network none \$IMG python3 -c "import socket,time;t=time.time();exec('try:\n socket.getaddrinfo(\"pypi.org\",443)\nexcept OSError as e: print(type(e).__name__)');print(round(time.time()-t,1))"

echo "##### una invocazione completa con --debug (ultime 40 righe)"
timeout -k 5 90 apptainer --debug exec --containall --cleanenv --no-home --no-mount bind-paths --net --network none --pwd /work --bind \$BEEGFS:/work:rw \$IMG true < /dev/null > /tmp/diag_dbg_\$\$ 2>&1
echo "rc=\$?"; tail -n 40 /tmp/diag_dbg_\$\$

echo "##### processi apptainer rimasti sul nodo"
ps -u \$USER -o pid,etime,cmd | grep -E "apptainer|starter|squashfuse" | grep -v grep || echo "nessuno"
rm -rf \$BEEGFS \$LOCAL /tmp/diag_out_\$\$ /tmp/diag_dbg_\$\$
EOF
