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
#
# MCGMark (audit di MCGMark, D18, D20):
#   bash scripts/oracle_method.sh mcgmark select  # una volta: 3 problemi CodeNet (defq), poi commit
#                                                 # di configs/dataset/codenet_excluded.yaml
#   bash scripts/oracle_method.sh mcgmark 1/2     # persona 1 (anche dallo stesso clone)
#   bash scripts/oracle_method.sh mcgmark 2/2     # persona 2, in qualunque momento
#   bash scripts/oracle_method.sh mcgmark test    # una sola persona, dopo che entrambe le parti
#                                                 # sono finite: unisce le parti ed esegue i test
# Senza secondo argomento: tutto in un job. Ogni parte genera, per i suoi prompt:
#   - HumanEval (inputs.json): codice originale (sola 0000) e codice del framework (0000-0003),
#     con forzatura del testo (D18);
#   - codice lungo (inputs_long.json: 3 CodeNet + 6 ClassEval): 0000-0002 e 0000-0003, senza
#     forzatura, per testo identico e tempi (patch 0003).
# Le parti vanno in $WMB/oracle_parts/<metodo> (cartella condivisa fra i due utenti).
#
# PromptMark (audit di PromptMark; prima la fase promptmark_freq): stesse modalità
#   bash scripts/oracle_method.sh promptmark [1/2 | 2/2 | test]
# Ogni parte esegue il percorso diretto (expI senza shim e senza limiti) sui suoi prompt; "test"
# unisce le parti, confronta la patch 0002 con l'originale (CPU) ed esegue i test.
set -uo pipefail
umask 002  # cartella delle parti condivisa fra i due utenti

METHOD="${1:?usage: oracle_method.sh METHOD [K/M | test | select]}"
MODE="${2:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
METHOD_PY="$WMB/miniforge3/envs/$METHOD/bin/python"
FIX="tests/fixtures/oracle/$METHOD"
PARTS="$WMB/oracle_parts/$METHOD"
ENV_SETUP="umask 002
        export PATH=$WMB/bench-core/bin:\$PATH
        export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
        export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
        cd $REPO_ROOT"
INPUTS="python tests/oracle/make_oracle_inputs.py $METHOD"
TESTS="WMB_REQUIRE_DATA=1 python -m pytest -q -rs -rP -m oracle tests/oracle/test_${METHOD}_oracle.py"
TIME_MIN=90
GIT=(git -c "safe.directory=*")  # il clone può appartenere all'altro utente

# Copia del submodule di MCGMark con le patch indicate (in ordine).
mcgmark_copy() {
    local rel="$1"
    shift
    rm -rf "${REPO_ROOT:?}/$rel" && mkdir -p "$REPO_ROOT/$rel"
    "${GIT[@]}" -C "$REPO_ROOT/third_party/MCGMT" checkout-index -a -f --prefix="$REPO_ROOT/$rel/" || return 1
    local patch
    for patch in "$@"; do
        (cd "$REPO_ROOT" && "${GIT[@]}" apply --directory="$rel" "patches/mcgmark/$patch") || return 1
    done
}

case "$METHOD" in
    sweet)
        IO="--inputs $FIX/inputs.json --out $FIX/original.json"
        ORIGINAL="PYTHONPATH=$REPO_ROOT/build/patched/sweet $METHOD_PY tests/oracle/sweet_original.py $IO"
        ;;
    mcgmark)
        if [ "$MODE" = "select" ]; then
            srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 4 --mem 16G -t 60 \
                --job-name "wmb-oracle-select" bash -lc "
                    set -uo pipefail
                    $ENV_SETUP
                    python tests/oracle/select_codenet_oracle.py
                "
            exit $?
        fi
        if [ ! -f "$REPO_ROOT/build/patched/mcgmark/Watermark/watermark_global.py" ]; then
            echo "[ERROR] build/patched/mcgmark missing: run bash scripts/apply_patches.sh mcgmark" >&2
            exit 1
        fi
        SHARE="1/1"
        OUT="$FIX"
        if [[ "$MODE" =~ ^[0-9]+/[0-9]+$ ]]; then
            SHARE="$MODE"
            OUT="$PARTS"
            TESTS="true"
        elif [ "$MODE" = "test" ]; then
            :
        elif [ -n "$MODE" ]; then
            echo "usage: oracle_method.sh mcgmark [K/M | test | select]" >&2; exit 2
        fi
        LABEL="${SHARE%/*}of${SHARE#*/}"
        sfx() { if [ "$OUT" = "$PARTS" ]; then echo ".$LABEL.json"; else echo ".json"; fi; }
        INPUTS="$INPUTS && python tests/oracle/make_mcgmark_long_inputs.py"
        if [ "$MODE" = "test" ]; then
            INPUTS="$INPUTS && python tests/oracle/merge_oracle_parts.py $PARTS $FIX"
            ORIGINAL="true"
        else
            mkdir -p "$OUT"
            ORIG_SRC="build/oracle_src/mcgmark_original_$LABEL"
            NOSPEED_SRC="build/oracle_src/mcgmark_nospeed_$LABEL"
            mcgmark_copy "$ORIG_SRC" 0000-modifiche-preesistenti.patch || exit 1
            mcgmark_copy "$NOSPEED_SRC" 0000-modifiche-preesistenti.patch 0001-gamma-fisso.patch \
                0002-rimuovi-decodifica-vocabolario.patch || exit 1
            PATCHED_PATH="$REPO_ROOT/build/patched/mcgmark/Watermark"
            S="tests/oracle/mcgmark_original.py --share $SHARE"
            HE="$S --inputs $FIX/inputs.json"
            LONG="$S --inputs $FIX/inputs_long.json --no-forced"
            O_HE="$OUT/original$(sfx)"
            STEP1="PYTHONPATH=$REPO_ROOT/$ORIG_SRC/Watermark $METHOD_PY $HE --variant original --out $O_HE"
            STEP2="PYTHONPATH=$PATCHED_PATH $METHOD_PY $HE --variant patched --replay-from $O_HE"
            STEP2="$STEP2 --out $OUT/patched$(sfx)"
            STEP3="PYTHONPATH=$REPO_ROOT/$NOSPEED_SRC/Watermark $METHOD_PY $LONG --variant nospeed"
            STEP3="$STEP3 --out $OUT/nospeed_long$(sfx)"
            STEP4="PYTHONPATH=$PATCHED_PATH $METHOD_PY $LONG --variant patched --out $OUT/patched_long$(sfx)"
            ORIGINAL="$STEP1 && $STEP2 && $STEP3 && $STEP4"
        fi
        TIME_MIN=180
        ;;
    promptmark)
        # Prerequisito: fase promptmark_freq (lista di frequenza, D8).
        if [ ! -f "$REPO_ROOT/build/patched/promptmark/src/llm_providers.py" ]; then
            echo "[ERROR] build/patched/promptmark missing: run bash scripts/apply_patches.sh promptmark" >&2
            exit 1
        fi
        SHARE="1/1"
        OUT="$FIX"
        if [[ "$MODE" =~ ^[0-9]+/[0-9]+$ ]]; then
            SHARE="$MODE"
            OUT="$PARTS"
            TESTS="true"
        elif [ -n "$MODE" ] && [ "$MODE" != "test" ]; then
            echo "usage: oracle_method.sh promptmark [K/M | test]" >&2; exit 2
        fi
        LABEL="${SHARE%/*}of${SHARE#*/}"
        PM_PATH="$REPO_ROOT/build/patched/promptmark/src:$REPO_ROOT/shims"
        O_OUT="$OUT/original.json"
        if [ "$OUT" = "$PARTS" ]; then O_OUT="$OUT/original.$LABEL.json"; fi
        STEP_ORIG="PYTHONPATH=$PM_PATH $METHOD_PY tests/oracle/promptmark_original.py"
        STEP_ORIG="$STEP_ORIG --inputs $FIX/inputs.json --share $SHARE --out $O_OUT"
        # Niente bytecode in third_party/ (il codice originale si importa da lì in sola lettura).
        CHECK="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PM_PATH $METHOD_PY tests/oracle/promptmark_patch_check.py"
        CHECK="$CHECK --original $REPO_ROOT/third_party/PromptMark/src --patched $REPO_ROOT/build/patched/promptmark/src"
        CHECK="$CHECK --inputs $FIX/inputs.json --out $FIX/patch_check.json"
        if [ "$MODE" = "test" ]; then
            INPUTS="$INPUTS && python tests/oracle/merge_oracle_parts.py $PARTS $FIX"
            ORIGINAL="$CHECK"
        elif [ "$OUT" = "$PARTS" ]; then
            mkdir -p "$OUT"
            ORIGINAL="$STEP_ORIG"
        else
            ORIGINAL="$STEP_ORIG && $CHECK"
        fi
        TIME_MIN=180
        ;;
    *) echo "no oracle for method '$METHOD' yet" >&2; exit 2 ;;
esac

srun -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos --gres=gpu:1 -c 8 --mem 64G -t "$TIME_MIN" \
    --job-name "wmb-oracle-$METHOD" bash -lc "
        set -uo pipefail
        $ENV_SETUP
        echo \"##### oracle $METHOD ${MODE:-all}: nodo \$(hostname), \$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)\"
        ($INPUTS) || exit 1
        ($ORIGINAL) || exit 1
        $TESTS
    "
