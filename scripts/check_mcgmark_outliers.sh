#!/usr/bin/env bash
# Verifica della patch 0004 di MCGMark (outlier del paper, D32; scripts/check_mcgmark_outliers.py).
# Si lancia dal nodo di login, dopo `bash scripts/apply_patches.sh mcgmark`:
#
#   bash scripts/check_mcgmark_outliers.sh [MODEL]        # default qwen25_coder_7b
#
# Due job: generazione e rilevazione su gpuq (varianti off e on), poi esecuzione dei test su defq
# (parte solo se il primo è riuscito). A job finiti:
#   python scripts/check_mcgmark_outliers.py report [--model MODEL]
# Scrive solo in $WMB/tmp/mcgmark_outliers/<MODEL>/; log in $WMB/logs/check_mcgmark_outliers_<job>.log.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
MODEL="${1:-qwen25_coder_7b}"
if ! grep -q "wm-bench 0004" "$REPO_ROOT/build/patched/mcgmark/Watermark/watermark_processor.py"; then
    echo "build/patched/mcgmark without patch 0004: run bash scripts/apply_patches.sh mcgmark" >&2
    exit 1
fi
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
job_script() {  # $1 = modalità, $2 = righe in più prima del comando
    local path="$WMB/logs/check_mcgmark_outliers_$1.sbatch"
    cat > "$path" <<SBATCH
#!/bin/bash -l
set -e
umask 002
export PATH=$WMB/bench-core/bin:\$PATH
export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
$2
cd $REPO_ROOT
echo "##### check_mcgmark_outliers $1 $MODEL: nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
python scripts/check_mcgmark_outliers.py $1 --model $MODEL
SBATCH
    echo "$path"
}
GEN=$(sbatch --parsable -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos --gres=gpu:1 \
    -c 8 --mem 64G -t 240 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-check-mcgmark-outliers \
    -o "$WMB/logs/check_mcgmark_outliers_%j.log" "$(job_script generate "")")
EXE=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
    -c 16 --mem 32G -t 120 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --dependency "afterok:$GEN" \
    --job-name wmb-check-mcgmark-outliers-exec -o "$WMB/logs/check_mcgmark_outliers_%j.log" \
    "$(job_script execute "module load apptainer/apptainer.module")")
echo "jobs: generate $GEN (gpuq), execute $EXE (defq, after $GEN)"
echo "logs: $WMB/logs/check_mcgmark_outliers_$GEN.log, $WMB/logs/check_mcgmark_outliers_$EXE.log"
