#!/usr/bin/env bash
# Giro 4 su sviluppo (pipeline di multimark v1.3.9 e v1.3.10, regole fissate prima dei risultati;
# decisioni dell'utente del 20 novembre 2026). Libreria MultiMark 0664817 (opzioni
# canonical.natural_forms e names.suffix_by_type). Punti: {B, C, D} x {opzioni spente;
# suffix_by_type=on; on + natural_forms=const_left; on + const_left_zero} = 12, tutti nella griglia
# configs/hpo/multimark.yaml (scripts/posthoc_config_hashes.py):
#
#   B      a8a296957377  B_sfx  042968dc17df  B_sfx_cl  317adaa840ee  B_sfx_clz  a2d195001771
#   C      19e744a3b406  C_sfx  71d9d5e6160d  C_sfx_cl  a8fadfc4757a  C_sfx_clz  fb24f51f5d15
#   D      b4cd11f13a07  D_sfx  f4fb209f9226  D_sfx_cl  f765c166de34  D_sfx_clz  cb29e1d7b6b8
#
# Ogni invio è lo sweep di multimark (methods=[multimark], hpo.grid=multimark): nessuna cella di
# default (né mcss né la base di multimark si rilanciano con la libreria nuova). Solo split=dev,
# Python, L1 e L2, 2 modelli. Riferimenti già calcolati, solo letti dai report: mcss ae63f16de28f
# (3c47a1f) e le 7 configurazioni del giro di capacità (a4e6972: mcss b521002e09f5, multimark base
# 3c1bd11758fc, single_letter, A, B, C, D). Si lancia dal nodo di login, una fase alla volta:
#
#   bash scripts/run_p2_round4.sh watermark                 # 1) inserimento
#   bash scripts/run_p2_round4.sh execute_detect            # 2) test dei marcati, rilevazione
#   bash scripts/run_p2_round4.sh calibrate                 # 3) soglie L1+L2 (hash nuovi)
#   bash scripts/run_p2_round4.sh metrics                   # 4) metriche senza attacco
#   bash scripts/run_p2_round4.sh attack                    # 5) attacchi (21 punti)
#   bash scripts/run_p2_round4.sh imperceptibility          # 6) perplexity, logreg, unixcoder
#   bash scripts/run_p2_round4.sh attack_execute_detect     # 7) test e rilevazione degli attaccati
#   bash scripts/run_p2_round4.sh attack_metrics            # 8) metriche degli attaccati
#   bash scripts/run_p2_round4.sh imperceptibility_metrics  # 9) metrics_imperceptibility
#   bash scripts/run_p2_round4.sh report                    # 10) i cinque log *_round4.txt
#
# Le fasi 5 e 6 possono girare insieme, come le 8 e 9. Attacchi: insieme S di v1.3.3 (T1.4
# natural r=1,0; T1.4 random r=0,5; T1.5 k=5; T2.1 b=all; T2.4 p=0,5; T4.1 rename=true) più T1.6
# (caps=convert, caps=keep); la pipeline non sceglie singoli punti di una griglia, quindi si
# lanciano le griglie intere di T1.4, T1.5, T2.1, T2.4, T4.1 e T1.6 (21 punti) e i report usano
# solo i punti di S e di T1.6. Le celle attaccate e dell'impercettibilità dei punti entrano nello
# sweep (ADR-017 §6-§7). Soglie calibrate su dev per gli hash nuovi, più la soglia teorica.
#
# Prima di ogni invio lo script controlla che i job in coda più quelli da inviare non superino
# MAX_JOBS (10). Ogni fase controlla che la precedente sia finita (scripts/check_stage_done.py) e
# che nessun job di quella fase sia in coda. Nessuna fase scrive su un'uscita esistente; nella fase
# report un log già presente si salta.
#
# Variabili: JOBS (job per invio, default 3), JOBS_ATTACK (fasi 5, 7, 8; default 4), JOBS_IMP
# (fasi 6 e 9, per ognuna delle tre fasi; default 2), MAX_JOBS.
set -euo pipefail

PHASE="${1:?usage: run_p2_round4.sh watermark|execute_detect|calibrate|metrics|attack|imperceptibility|attack_execute_detect|attack_metrics|imperceptibility_metrics|report}"
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

# Libreria e punti del giro 4 (tests/unit/test_posthoc_config_hashes.py).
EXPECTED=06648176cee2cf125ae8446381e4049929894cf9
LIB=build/patched/multimark
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

check_hashes() {  # i punti della griglia devono essere i 12 scritti qui sopra
    local grid want
    grid="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only grid --expect-commit "$EXPECTED" | sort | paste -sd' ' -)"
    want="$(printf '%s\n' "$R4_B" "$R4_B_SFX" "$R4_B_SFX_CL" "$R4_B_SFX_CLZ" "$R4_C" "$R4_C_SFX" \
        "$R4_C_SFX_CL" "$R4_C_SFX_CLZ" "$R4_D" "$R4_D_SFX" "$R4_D_SFX_CL" "$R4_D_SFX_CLZ" | sort | paste -sd' ' -)"
    [ "$grid" = "$want" ] || die "multimark grid points are $grid, expected $want"
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
        echo "# $header (scripts/run_p2_round4.sh report): $(date -Iseconds)"
        echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED for the 12 points of round 4; references read from existing cells"
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
        echo "##### config_hash of round 4 (scripts/posthoc_config_hashes.py)"
        "$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --expect-commit "$EXPECTED" | grep ' grid:'
        no_jobs wmb-watermark
        room "$JOBS"
        submit --jobs "$JOBS" stage=watermark "${CLEAN[@]}"
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
        NEW=(--config "multimark:$R4_B:r4_B" --config "multimark:$R4_B_SFX:r4_B_sfx"
             --config "multimark:$R4_B_SFX_CL:r4_B_sfx_cl" --config "multimark:$R4_B_SFX_CLZ:r4_B_sfx_clz"
             --config "multimark:$R4_C:r4_C" --config "multimark:$R4_C_SFX:r4_C_sfx"
             --config "multimark:$R4_C_SFX_CL:r4_C_sfx_cl" --config "multimark:$R4_C_SFX_CLZ:r4_C_sfx_clz"
             --config "multimark:$R4_D:r4_D" --config "multimark:$R4_D_SFX:r4_D_sfx"
             --config "multimark:$R4_D_SFX_CL:r4_D_sfx_cl" --config "multimark:$R4_D_SFX_CLZ:r4_D_sfx_clz")
        OLD=(--config "mcss:$MCSS_NEW:mcss_new_3c47a1f" --config "mcss:$MCSS:mcss_old_a4e6972"
             --config "multimark:$MM:multimark" --config "multimark:$MM_SL:single_letter"
             --config "multimark:$MM_A:A" --config "multimark:$MM_B:B"
             --config "multimark:$MM_C:C" --config "multimark:$MM_D:D")
        DEV=(--levels L1 L2 --split dev --language python)
        report_log P2_rename_check_round4.txt "P2 rename check of the round 4 points" \
            scripts/check_rename_dev.py "${NEW[@]}" --multimark third_party/multimark \
            --expect-commit "${EXPECTED:0:7}" "${DEV[@]}"
        report_log P2_round4_capacity.txt "P2 round 4 on dev: S tables and curve in K" \
            scripts/report_round3.py "${OLD[@]}" "${NEW[@]}" \
            --curve mcss_new_3c47a1f multimark B r4_B r4_C r4_D "${DEV[@]}"
        report_log P2_snake_round4.txt "P2 T1.6 snake_case on dev, round 4" \
            scripts/report_snake_dev.py "${OLD[@]}" "${NEW[@]}" "${DEV[@]}"
        report_log P2_imperceptibility_round4.txt "P2 imperceptibility on dev, round 4" \
            scripts/report_imperceptibility_dev.py "${OLD[@]}" "${NEW[@]}" \
            --base multimark --r-point single_letter "${DEV[@]}"
        report_log P2_round4.txt "P2 round 4 on dev: the two working points of v1.3.10" \
            scripts/report_round4.py "${OLD[@]}" "${NEW[@]}" --base multimark \
            --mcss mcss_new_3c47a1f --multimark third_party/multimark \
            --expect-commit "${EXPECTED:0:7}" "${DEV[@]}"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
