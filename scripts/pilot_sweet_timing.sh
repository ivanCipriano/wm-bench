#!/usr/bin/env bash
# Pilota dei tempi di SWEET con Qwen (scripts/pilot_sweet_timing.py): un job su gpuq (A100) e uno su
# aiq (H100), stessi dati di sviluppo. Dal nodo di login:
#
#   bash scripts/pilot_sweet_timing.sh
#
# A job finiti:
#   python scripts/pilot_sweet_timing.py report | tee docs/milestone_logs/M13_sweet_timing.txt
# (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)
# Scrive solo in $WMB/tmp/sweet_timing/; log in $WMB/logs/pilot_sweet_timing_<job>.log.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
SCRIPT="$WMB/logs/pilot_sweet_timing.sbatch"
cat > "$SCRIPT" <<SBATCH
#!/bin/bash -l
set -e
umask 002
export PATH=$WMB/bench-core/bin:\$PATH
export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
cd $REPO_ROOT
echo "##### pilot_sweet_timing: nodo \$(hostname), partizione \$SLURM_JOB_PARTITION, commit \$(git rev-parse --short HEAD)"
python scripts/pilot_sweet_timing.py run
SBATCH
for PART in gpuq aiq; do
    JOB=$(sbatch --parsable -p "$PART" -A did_tesi_nlp_330 --qos "did_tesi_nlp_330_${PART}_qos" \
        --gres=gpu:1 -c 8 --mem 64G -t 120 ${EXCLUDE[@]+"${EXCLUDE[@]}"} \
        --job-name "wmb-pilot-sweet-timing-$PART" -o "$WMB/logs/pilot_sweet_timing_%j.log" "$SCRIPT")
    echo "job $JOB ($PART); log: $WMB/logs/pilot_sweet_timing_$JOB.log"
done
