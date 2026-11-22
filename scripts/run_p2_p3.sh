#!/usr/bin/env bash
# Giro P3 su sviluppo (pipeline di multimark v1.3.13, decisioni prima dei risultati del 22 novembre
# 2026). Libreria MultiMark b2b6c18 (proponente neurale dei nomi, MD31): le opzioni di B e di D
# (come r4_B e r4_D, opzioni del giro 4 spente) con names.proposer=neural, modello
# qwen25_coder_1p5b del catalogo (Qwen/Qwen2.5-Coder-1.5B-Instruct, snapshot 2e1fd397). Sono gli
# unici 2 punti della griglia configs/hpo/multimark.yaml (scripts/posthoc_config_hashes.py):
#
#   p3_B  d54101565a0b    p3_D  e80cde8924cb
#
# Ogni invio è lo sweep di multimark (methods=[multimark], hpo.grid=multimark): nessuna cella di
# default. Solo split=dev, Python, L1 e L2, 2 modelli. Riferimenti già calcolati, solo letti dai
# report: mcss ae63f16de28f (3c47a1f), le 7 configurazioni del giro di capacità (a4e6972: mcss
# b521002e09f5 e 6 multimark) e i 12 punti del giro 4 (0664817). Si lancia dal nodo di login, una
# fase alla volta:
#
#   bash scripts/run_p2_p3.sh watermark                 # 1) inserimento, un job GPU
#   bash scripts/run_p2_p3.sh execute_detect            # 2) test dei marcati, rilevazione
#   bash scripts/run_p2_p3.sh calibrate                 # 3) soglie L1+L2 (hash nuovi)
#   bash scripts/run_p2_p3.sh metrics                   # 4) metriche senza attacco
#   bash scripts/run_p2_p3.sh attack                    # 5) attacchi (21 punti)
#   bash scripts/run_p2_p3.sh imperceptibility          # 6) perplexity, logreg, unixcoder
#   bash scripts/run_p2_p3.sh attack_execute_detect     # 7) test e rilevazione degli attaccati
#   bash scripts/run_p2_p3.sh attack_metrics            # 8) metriche degli attaccati
#   bash scripts/run_p2_p3.sh imperceptibility_metrics  # 9) metrics_imperceptibility
#   bash scripts/run_p2_p3.sh report                    # 10) i cinque log P3_*.txt
#
# Inserimento: la regola delle risorse è per metodo (configs/resources/default.yaml: multimark su
# CPU), quindi solo questo invio la cambia (GPU_EMBED) e manda le 8 celle su GPU in **un solo job**
# (--jobs 1): le celle sono in sequenza (modello, livello) x (B, D), quindi ogni cella di D viene
# subito dopo quella di B sugli stessi codici della baseline e legge dalla cache su disco dello
# shim (shims/bench_shims/multimark/neural_cache.py, in <artifacts>/_cache/multimark_neural_names)
# le risposte del modello invece di rigenerarle. La chiave della cache ha lotto, modello con
# snapshot, prompt e parametri di generazione: le risposte riusate sono quelle che D avrebbe
# generato sulla stessa GPU. Con più job le celle di B e di D finirebbero in job paralleli e la
# cache non servirebbe. La rilevazione e le altre fasi restano su CPU (il bit si legge dal nome).
# WMB_GPU sceglie la GPU come per le altre fasi (default a100, gpuq).
#
# Le fasi 5 e 6 possono girare insieme, come le 8 e 9. Attacchi: insieme S di v1.3.3 (T1.4
# natural r=1,0; T1.4 random r=0,5; T1.5 k=5; T2.1 b=all; T2.4 p=0,5; T4.1 rename=true) più T1.6
# (caps=convert, caps=keep); si lanciano le griglie intere di T1.4, T1.5, T2.1, T2.4, T4.1 e T1.6
# (21 punti) e i report usano solo i punti di S e di T1.6. Soglie calibrate su dev per gli hash
# nuovi, più la soglia teorica.
#
# Prima di ogni invio lo script controlla che i job in coda più quelli da inviare non superino
# MAX_JOBS (10). Ogni fase controlla che la precedente sia finita (scripts/check_stage_done.py) e
# che nessun job di quella fase sia in coda. Nessuna fase scrive su un'uscita esistente; nella fase
# report un log già presente si salta.
#
# Variabili: JOBS (job per invio, default 3), JOBS_ATTACK (fasi 5, 7, 8; default 4), JOBS_IMP
# (fasi 6 e 9, per ognuna delle tre fasi; default 2), MAX_JOBS.
set -euo pipefail

PHASE="${1:?usage: run_p2_p3.sh watermark|execute_detect|calibrate|metrics|attack|imperceptibility|attack_execute_detect|attack_metrics|imperceptibility_metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-3}"
JOBS_ATTACK="${JOBS_ATTACK:-4}"
JOBS_IMP="${JOBS_IMP:-2}"
MAX_JOBS="${MAX_JOBS:-10}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)

# Libreria e punti del giro P3 (tests/unit/test_posthoc_config_hashes.py).
EXPECTED=b2b6c18ea5303ab134f84f11d8b1de1c2bb5ec5a
LIB=build/patched/multimark
P3_B=d54101565a0b
P3_D=e80cde8924cb
# Riferimenti già calcolati, solo letti dai report: giro 4 (0664817).
R4_B=a8a296957377
R4_B_SFX=042968dc17df
R4_B_SFX_CL=317adaa840ee
R4_B_SFX_CLZ=a2d195001771
R4_C=19e744a3b406
R4_C_SFX=71d9d5e6160d
R4_C_SFX_CL=a8fadfc4757a
R4_C_SFX_CLZ=fb24f51f5d15
R4_D=b4cd11f13a07
R4_D_SFX=f4fb209f9226
R4_D_SFX_CL=f765c166de34
R4_D_SFX_CLZ=cb29e1d7b6b8
# Riferimenti già calcolati, solo letti dai report.
MCSS_NEW=ae63f16de28f
MCSS=b521002e09f5
MM=3c1bd11758fc
MM_SL=4937d9ddd78a
MM_A=770f999adefb
MM_B=cb79b2fcb4e6
MM_C=cf7e4c9f106e
MM_D=37400fdcd589
LOGS=docs/milestone_logs

ATTACKS="attacks=[T1.4,T1.5,T2.1,T2.4,T4.1,T1.6]"
CLEAN=("methods=[multimark]" "hpo.grid=multimark" "levels=[L1,L2]" "splits=[dev]" "languages=[python]")
ATTACKED=("${CLEAN[@]}" "$ATTACKS")
IMP=("${CLEAN[@]}" "attacks=[]")
PPL_SOURCES="execution.sources=[llm_baseline,llm_watermarked]"
# Solo per l'inserimento: multimark su GPU (proponente neurale).
GPU_EMBED="resources.gpu_methods_by_stage.watermark.multimark=true"

die() { echo "[STOP] $*" >&2; exit 1; }

check_library() {
    local link head copy
    link="$(git ls-files --stage -- third_party/multimark | awk '$1 == "160000" {print $2}')"
    head="$(git -C third_party/multimark rev-parse HEAD 2>/dev/null || echo none)"
    copy="$(cat "$LIB.source_commit" 2>/dev/null || echo none)"
    [ "$link" = "$EXPECTED" ] || die "submodule recorded at $link, expected $EXPECTED: git pull first"
    [ "$head" = "$EXPECTED" ] || die "third_party/multimark checked out at $head: run git submodule update --init third_party/multimark"
    [ "$copy" = "$EXPECTED" ] || die "$LIB at $copy: run bash scripts/apply_patches.sh multimark"
}

check_hashes() {  # i punti della griglia devono essere i 2 scritti qui sopra, in quest'ordine
    local grid want
    grid="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only grid --expect-commit "$EXPECTED" | paste -sd' ' -)"
    want="$P3_B $P3_D"
    [ "$grid" = "$want" ] || die "multimark grid points are $grid, expected $want (B before D)"
}

no_jobs() {  # $1 = prefisso del nome dei job ancora in coda o in esecuzione
    local running
    running="$(squeue -h -u "$USER" -o '%i %j' 2>/dev/null | awk -v p="$1" '$2 == p || index($2, p ".") == 1 || index($2, p "-") == 1' || true)"
    [ -z "$running" ] || die "jobs still queued or running for $1: $(echo "$running" | paste -sd' ' -)"
}

room() {  # $1 = job che la fase invia al più
    local queued
    queued="$(squeue -h -u "$USER" 2>/dev/null | wc -l || echo 0)"
    [ $((queued + $1)) -le "$MAX_JOBS" ] || die "$queued jobs in queue + $1 to submit > $MAX_JOBS: wait for some jobs to finish"
}

done_cells() {  # $1 = fase; altri argomenti = override di check_stage_done
    local stage="$1"
    shift
    no_jobs "wmb-$stage"
    "$BENCH_PY" scripts/check_stage_done.py "stage=$stage" "$@" || die "$stage not finished"
}

submit() {  # $@ = argomenti di bench submit
    local out
    out="$(bench submit "$@")" || { echo "$out" >&2; die "bench submit failed"; }
    echo "$out" | grep -v "parameters:" >&2
}

report_log() {  # $1 = nome del log, $2 = intestazione; altri argomenti = comando del report
    local name="$1" header="$2" tmp
    shift 2
    if [ -e "$LOGS/$name" ]; then
        echo "[SKIP] $LOGS/$name already exists (not overwritten)"
        return 0
    fi
    tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
    "${SRUN[@]}" -c 2 --mem 16G -t 240 "$BENCH_PY" "$@" --report "$tmp"
    {
        echo "# $header (scripts/run_p2_p3.sh report): $(date -Iseconds)"
        echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED for the 2 points of P3; references read from existing cells"
        cat "$tmp"
    } > "$tmp.log"
    rm -f "$tmp"
    mv -n "$tmp.log" "$LOGS/$name"
    [ ! -e "$tmp.log" ] || die "$LOGS/$name appeared meanwhile: output left in $tmp.log"
    echo "[OK] $LOGS/$name"
}

check_library
check_hashes
mkdir -p "$WMB/logs"
case "$PHASE" in
    watermark)
        echo "##### config_hash of P3 (scripts/posthoc_config_hashes.py)"
        "$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --expect-commit "$EXPECTED" | grep ' grid:'
        no_jobs wmb-watermark
        room 1
        submit --jobs 1 stage=watermark "${CLEAN[@]}" "$GPU_EMBED"
        ;;
    execute_detect)
        done_cells watermark "${CLEAN[@]}"
        room $((2 * JOBS))
        submit --jobs "$JOBS" stage=execute "${CLEAN[@]}" "execution.sources=[llm_watermarked]"
        submit --jobs "$JOBS" stage=detect "${CLEAN[@]}"
        ;;
    calibrate)
        done_cells execute "${CLEAN[@]}" "execution.sources=[llm_watermarked]"
        done_cells detect "${CLEAN[@]}"
        room "$JOBS"
        submit --jobs "$JOBS" stage=calibrate "${CLEAN[@]}"
        ;;
    metrics)
        done_cells calibrate "${CLEAN[@]}"
        room "$JOBS"
        submit --jobs "$JOBS" stage=metrics "${CLEAN[@]}"
        ;;
    attack)
        done_cells metrics "${CLEAN[@]}"
        done_cells attack_pools "languages=[python]"
        no_jobs wmb-attack
        room "$JOBS_ATTACK"
        submit --jobs "$JOBS_ATTACK" stage=attack "${ATTACKED[@]}"
        ;;
    imperceptibility)
        done_cells metrics "${CLEAN[@]}"
        for stage in perplexity adversarial_logreg adversarial_unixcoder; do no_jobs "wmb-$stage"; done
        room $((3 * JOBS_IMP))
        submit --jobs "$JOBS_IMP" stage=perplexity "${IMP[@]}" "$PPL_SOURCES"
        submit --jobs "$JOBS_IMP" stage=adversarial_logreg "${IMP[@]}"
        submit --jobs "$JOBS_IMP" stage=adversarial_unixcoder "${IMP[@]}"
        ;;
    attack_execute_detect)
        done_cells attack "${ATTACKED[@]}"
        room $((2 * JOBS_ATTACK))
        submit --jobs "$JOBS_ATTACK" stage=execute "${ATTACKED[@]}" "execution.sources=[attacked]"
        submit --jobs "$JOBS_ATTACK" stage=detect "${ATTACKED[@]}"
        ;;
    attack_metrics)
        done_cells execute "${ATTACKED[@]}" "execution.sources=[attacked]"
        done_cells detect "${ATTACKED[@]}"
        room "$JOBS_ATTACK"
        submit --jobs "$JOBS_ATTACK" stage=metrics "${ATTACKED[@]}"
        ;;
    imperceptibility_metrics)
        done_cells perplexity "${IMP[@]}" "$PPL_SOURCES"
        done_cells adversarial_logreg "${IMP[@]}"
        done_cells adversarial_unixcoder "${IMP[@]}"
        no_jobs wmb-metrics_imperceptibility
        room "$JOBS_IMP"
        submit --jobs "$JOBS_IMP" stage=metrics_imperceptibility "${IMP[@]}"
        ;;
    report)
        done_cells metrics "${ATTACKED[@]}"
        done_cells metrics_imperceptibility "${IMP[@]}"
        room 1
        NEW=(--config "multimark:$P3_B:p3_B" --config "multimark:$P3_D:p3_D")
        OLD=(--config "mcss:$MCSS_NEW:mcss_new_3c47a1f" --config "mcss:$MCSS:mcss_old_a4e6972"
             --config "multimark:$MM:multimark" --config "multimark:$MM_SL:single_letter"
             --config "multimark:$MM_A:A" --config "multimark:$MM_B:B"
             --config "multimark:$MM_C:C" --config "multimark:$MM_D:D"
             --config "multimark:$R4_B:r4_B" --config "multimark:$R4_B_SFX:r4_B_sfx"
             --config "multimark:$R4_B_SFX_CL:r4_B_sfx_cl" --config "multimark:$R4_B_SFX_CLZ:r4_B_sfx_clz"
             --config "multimark:$R4_C:r4_C" --config "multimark:$R4_C_SFX:r4_C_sfx"
             --config "multimark:$R4_C_SFX_CL:r4_C_sfx_cl" --config "multimark:$R4_C_SFX_CLZ:r4_C_sfx_clz"
             --config "multimark:$R4_D:r4_D" --config "multimark:$R4_D_SFX:r4_D_sfx"
             --config "multimark:$R4_D_SFX_CL:r4_D_sfx_cl" --config "multimark:$R4_D_SFX_CLZ:r4_D_sfx_clz")
        DEV=(--levels L1 L2 --split dev --language python)
        report_log P3_rename_check_dev.txt "P3 rename check of the neural points" \
            scripts/check_rename_dev.py "${NEW[@]}" --multimark third_party/multimark \
            --expect-commit "${EXPECTED:0:7}" "${DEV[@]}"
        report_log P3_capacity_dev.txt "P3 on dev: S tables and curve in K" \
            scripts/report_round3.py "${OLD[@]}" "${NEW[@]}" \
            --curve mcss_new_3c47a1f multimark r4_B r4_D p3_B p3_D "${DEV[@]}"
        report_log P3_snake_dev.txt "P3 T1.6 snake_case on dev" \
            scripts/report_snake_dev.py "${OLD[@]}" "${NEW[@]}" "${DEV[@]}"
        report_log P3_imperceptibility_dev.txt "P3 imperceptibility on dev" \
            scripts/report_imperceptibility_dev.py "${OLD[@]}" "${NEW[@]}" \
            --base multimark --r-point single_letter "${DEV[@]}"
        report_log P3_dev.txt "P3 on dev: the two rules of v1.3.10 over all measured points" \
            scripts/report_p3.py "${OLD[@]}" "${NEW[@]}" --base multimark \
            --mcss mcss_new_3c47a1f --multimark third_party/multimark \
            --expect-commit "${EXPECTED:0:7}" "${DEV[@]}"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
