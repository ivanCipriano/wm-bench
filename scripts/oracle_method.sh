#!/usr/bin/env bash
# Oracle di un metodo (SPEC §21.4) su un nodo gpuq. Si lancia dal nodo di login:
#
#   bash scripts/oracle_method.sh sweet 2>&1 | tee -a docs/milestone_logs/M6_cluster.txt
#
# 1. input fissi (bench-core): tests/fixtures/oracle/<metodo>/inputs.json
# 2. percorso originale (ambiente del metodo): tests/fixtures/oracle/<metodo>/original.json
# 3. test oracle (bench-core): shim contro originale, decoding, equivalenza per riga
# Le fixture vanno poi aggiunte al repository (git add tests/fixtures/oracle/<metodo>).
# Per STONE resta scripts/oracle_stone.sh (Milestone 5).
set -uo pipefail

METHOD="${1:?usage: oracle_method.sh METHOD}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
METHOD_PY="$WMB/miniforge3/envs/$METHOD/bin/python"
case "$METHOD" in
    sweet) SOURCE="$REPO_ROOT/build/patched/sweet" ;;
    *) echo "no oracle for method '$METHOD' yet" >&2; exit 2 ;;
esac

srun -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos --gres=gpu:1 -c 8 --mem 64G -t 90 \
    --job-name "wmb-oracle-$METHOD" bash -lc "
        set -uo pipefail
        umask 002
        export PATH=$WMB/bench-core/bin:\$PATH
        export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
        export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
        cd $REPO_ROOT
        echo \"##### oracle $METHOD: nodo \$(hostname), \$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)\"
        python tests/oracle/make_oracle_inputs.py $METHOD || exit 1
        PYTHONPATH=$SOURCE $METHOD_PY tests/oracle/${METHOD}_original.py \
            --inputs tests/fixtures/oracle/$METHOD/inputs.json --out tests/fixtures/oracle/$METHOD/original.json || exit 1
        WMB_REQUIRE_DATA=1 python -m pytest -q -rs -rP -m oracle tests/oracle/test_${METHOD}_oracle.py
    "
