#!/usr/bin/env bash
# Oracle di STONE (SPEC §21.4) su un nodo gpuq. Si lancia dal nodo di login:
#
#   bash scripts/oracle_stone.sh 2>&1 | tee -a docs/milestone_logs/M5_cluster.txt
#
# 1. input fissi (bench-core): tests/fixtures/oracle/stone/inputs.json
# 2. percorso originale (ambiente stone): tests/fixtures/oracle/stone/original.json
# 3. test oracle (bench-core): shim contro originale, decoding, equivalenza per riga
# Le fixture vanno poi aggiunte al repository (git add tests/fixtures/oracle/stone).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
STONE_PY="$WMB/miniforge3/envs/stone/bin/python"

srun -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos --gres=gpu:1 -c 8 --mem 64G -t 90 \
    --job-name wmb-oracle-stone bash -lc "
        set -uo pipefail
        umask 002
        export PATH=$WMB/bench-core/bin:\$PATH
        export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
        export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
        cd $REPO_ROOT
        echo \"##### oracle STONE: nodo \$(hostname), \$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)\"
        python tests/oracle/make_stone_inputs.py || exit 1
        PYTHONPATH=$REPO_ROOT/build/patched/stone/stone_implementation $STONE_PY tests/oracle/stone_original.py \
            --inputs tests/fixtures/oracle/stone/inputs.json --out tests/fixtures/oracle/stone/original.json || exit 1
        WMB_REQUIRE_DATA=1 python -m pytest -q -rs -rP -m oracle tests/oracle/test_stone_oracle.py
    "
