#!/usr/bin/env bash
# Anteprima dei prompt T2.2 (bozza) su 5 campioni reali (scripts/preview_t2_2.py). Dal nodo di login:
#
#   bash scripts/preview_t2_2.sh
#
# Un job su gpuq (Llama-3.1-8B-Instruct, 10 riscritture, pochi minuti). A job finito:
#   cat $WMB/tmp/t2_2_preview/preview.txt
# Scrive solo in $WMB/tmp/t2_2_preview/; log in $WMB/logs/preview_t2_2_<job>.log.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
SCRIPT="$WMB/logs/preview_t2_2.sbatch"
cat > "$SCRIPT" <<SBATCH
#!/bin/bash -l
set -e
umask 002
export PATH=$WMB/bench-core/bin:\$PATH
export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
cd $REPO_ROOT
echo "##### preview_t2_2: nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
python scripts/preview_t2_2.py
SBATCH
JOB=$(sbatch --parsable -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos --gres=gpu:1 \
    -c 8 --mem 64G -t 60 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-preview-t2_2 \
    -o "$WMB/logs/preview_t2_2_%j.log" "$SCRIPT")
echo "job: $JOB (gpuq); log: $WMB/logs/preview_t2_2_$JOB.log"
echo "a job finito: cat $WMB/tmp/t2_2_preview/preview.txt"
