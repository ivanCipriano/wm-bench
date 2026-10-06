#!/usr/bin/env bash
# Oracle di un metodo (SPEC §21.4) su un nodo gpuq. Si lancia dal nodo di login:
#
#   bash scripts/oracle_method.sh sweet 2>&1 | tee -a docs/milestone_logs/M6_cluster.txt
#
# 1. input fissi (bench-core): tests/fixtures/oracle/<metodo>/inputs.json
# 2. percorso originale (ambiente del metodo): tests/fixtures/oracle/<metodo>/original.json
#    (MCGMark: anche patched.json, e original.json sul codice con la sola patch 0000,
#    copiato in build/oracle_src/mcgmark)
# 3. test oracle (bench-core): shim contro originale, decoding, equivalenza per riga
# Le fixture vanno poi aggiunte al repository (git add tests/fixtures/oracle/<metodo>).
# Per STONE resta scripts/oracle_stone.sh (Milestone 5).
#
# MCGMark si può dividere fra più persone (job paralleli, ognuno con il proprio clone):
#
#   bash scripts/oracle_method.sh mcgmark 1/2    # persona 1: prompt e codici di indice pari
#   bash scripts/oracle_method.sh mcgmark 2/2    # persona 2, in contemporanea
#   bash scripts/oracle_method.sh mcgmark test   # una sola persona, dopo che entrambi hanno finito
#
# Le parti vanno in $WMB/oracle_parts/mcgmark (cartella condivisa); "test" le unisce nelle
# fixture del clone di chi lo lancia ed esegue i test. Senza secondo argomento: tutto in un job.
set -uo pipefail
umask 002  # cartella delle parti condivisa fra i due utenti

METHOD="${1:?usage: oracle_method.sh METHOD [K/M | test]}"
MODE="${2:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
METHOD_PY="$WMB/miniforge3/envs/$METHOD/bin/python"
FIX="tests/fixtures/oracle/$METHOD"
PARTS="$WMB/oracle_parts/$METHOD"
INPUTS="python tests/oracle/make_oracle_inputs.py $METHOD"
TESTS="WMB_REQUIRE_DATA=1 python -m pytest -q -rs -rP -m oracle tests/oracle/test_${METHOD}_oracle.py"
case "$METHOD" in
    sweet)
        IO="--inputs $FIX/inputs.json --out $FIX/original.json"
        ORIGINAL="PYTHONPATH=$REPO_ROOT/build/patched/sweet $METHOD_PY tests/oracle/sweet_original.py $IO"
        ;;
    mcgmark)
        SHARE="1/1"
        ORIG_OUT="$FIX/original.json"
        PATCHED_OUT="$FIX/patched.json"
        if [[ "$MODE" =~ ^[0-9]+/[0-9]+$ ]]; then
            SHARE="$MODE"
            mkdir -p "$PARTS"
            ORIG_OUT="$PARTS/original.${SHARE%/*}of${SHARE#*/}.json"
            PATCHED_OUT="$PARTS/patched.${SHARE%/*}of${SHARE#*/}.json"
            rm -f "$ORIG_OUT" "$PATCHED_OUT"
            TESTS="true"
        elif [ "$MODE" = "test" ]; then
            INPUTS="$INPUTS && python tests/oracle/merge_oracle_parts.py $PARTS $FIX"
        elif [ -n "$MODE" ]; then
            echo "usage: oracle_method.sh mcgmark [K/M | test]" >&2; exit 2
        fi
        # Codice originale per D18: submodule + sola patch 0000 (import e simboli mancanti).
        ORIG_SRC="$REPO_ROOT/build/oracle_src/mcgmark"
        rm -rf "$ORIG_SRC" && mkdir -p "$ORIG_SRC"
        git -C "$REPO_ROOT/third_party/MCGMT" checkout-index -a -f --prefix="$ORIG_SRC/"
        (cd "$REPO_ROOT" \
            && git apply --directory=build/oracle_src/mcgmark patches/mcgmark/0000-modifiche-preesistenti.patch) \
            || exit 1
        SCRIPT="tests/oracle/mcgmark_original.py --inputs $FIX/inputs.json --share $SHARE"
        RUN_ORIG="PYTHONPATH=$ORIG_SRC/Watermark $METHOD_PY $SCRIPT --variant original --out $ORIG_OUT"
        RUN_PATCHED="PYTHONPATH=$REPO_ROOT/build/patched/mcgmark/Watermark $METHOD_PY $SCRIPT --variant patched"
        RUN_PATCHED="$RUN_PATCHED --replay-from $ORIG_OUT --out $PATCHED_OUT"
        ORIGINAL="$RUN_ORIG && $RUN_PATCHED"
        if [ "$MODE" = "test" ]; then
            ORIGINAL="true"
        fi
        ;;
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
        echo \"##### oracle $METHOD ${MODE:-all}: nodo \$(hostname), \$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)\"
        ($INPUTS) || exit 1
        ($ORIGINAL) || exit 1
        $TESTS
    "
