#!/usr/bin/env bash
# Verifica degli attacchi T1 sulle canoniche di L1 (scripts/check_attacks_canonical.py, ADR-012)
# in un job su defq (CPU + Apptainer). Si lancia dal nodo di login, DOPO setup_tools.sh e la fase
# attack_pools:
#
#   LOG=M10_canonical_attacks_2.txt bash scripts/check_attacks_canonical.sh   # tutto T1
#   LOG=M11_t2_1_java.txt bash scripts/check_attacks_canonical.sh --languages java --attacks T2.1
#
# LOG (obbligatorio) è il nome di un file NUOVO in docs/milestone_logs/: lo script si ferma se
# esiste già, così un log committato non si riscrive. Il log del job va in
# $WMB/logs/check_attacks_canonical_<jobid>.log; a job finito si copia in docs/milestone_logs/$LOG
# (comando stampato qui sotto, con cp -n). Il job è uno script
# bash di login (`bash -l`): `module` esiste solo lì, non nella shell `sh` di `sbatch --wrap`.
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
JOB_SCRIPT="$WMB/logs/check_attacks_canonical.sbatch"
cat > "$JOB_SCRIPT" <<EOF
#!/bin/bash -l
set -e
module load apptainer/apptainer.module
export PATH=$WMB/bench-core/bin:\$PATH
cd $REPO_ROOT
umask 002
echo "##### nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
python scripts/check_attacks_canonical.py $*
EOF
JOB=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
    -c 16 --mem 64G -t 420 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-check-attacks-canonical \
    -o "$WMB/logs/check_attacks_canonical_%j.log" "$JOB_SCRIPT")
echo "job $JOB submitted; log: $WMB/logs/check_attacks_canonical_$JOB.log"
echo "a job finito: cp -n $WMB/logs/check_attacks_canonical_$JOB.log $DEST"
