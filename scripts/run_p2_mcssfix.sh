#!/usr/bin/env bash
# Rifacimento di mcss su sviluppo con la libreria MultiMark corretta (3c47a1f: la rinomina non
# tocca più letture di variabili omonime di altri scope). Decisione dell'utente del 19 novembre
# 2026, con la regola fissata prima di vedere i numeri: P2_rename_check_dev.txt dà mcss sopra l'1%
# (deepseek L2 1,67%), tutte le configurazioni multimark sotto; si rifà SOLO mcss. multimark resta
# a a4e6972: le sue celle si leggono con i vecchi hash e non si ricalcolano.
#
#   mcss vecchio b521002e09f5 (a4e6972) -> mcss nuovo ae63f16de28f (3c47a1f)
#
# Ogni invio usa methods=[mcss], mai hpo.grid; solo split=dev, Python, L1 e L2, 2 modelli. Si
# lancia dal nodo di login, una fase alla volta (ognuna controlla che la precedente sia finita):
#
#   bash scripts/run_p2_mcssfix.sh watermark                 # 1) inserimento
#   bash scripts/run_p2_mcssfix.sh execute_detect            # 2) test dei marcati, rilevazione
#   bash scripts/run_p2_mcssfix.sh calibrate                 # 3) soglia del nuovo hash (solo per
#                                                            #    il confronto della fase 4)
#   bash scripts/run_p2_mcssfix.sh metrics                   # 4) scripts/compare_thresholds.py
#                                                            #    (si ferma se negativi o soglie
#                                                            #    differiscono), poi metriche
#   bash scripts/run_p2_mcssfix.sh attack                    # 5) attacchi (25 punti)
#   bash scripts/run_p2_mcssfix.sh imperceptibility          # 6) perplexity, logreg, unixcoder
#   bash scripts/run_p2_mcssfix.sh attack_execute_detect     # 7) test e rilevazione degli attaccati
#   bash scripts/run_p2_mcssfix.sh attack_metrics            # 8) metriche degli attaccati
#   bash scripts/run_p2_mcssfix.sh imperceptibility_metrics  # 9) metrics_imperceptibility
#   bash scripts/run_p2_mcssfix.sh report                    # 10) i cinque log *_mcssfix.txt
#
# Le fasi 5 e 6 possono girare insieme, come le 8 e 9. Attacchi: l'insieme del controllo di
# robustezza su dev (T1.1, T1.2, T1.3, T1.4, T1.5, T2.1, T2.4, T4.1) più T1.6, griglie intere.
# Soglie: nessuna nuova calibrazione nel senso del protocollo; la fase 3 rifà la calibrazione del
# nuovo hash solo perché la soglia è legata all'hash, e la fase 4 verifica che sia identica a quella
# del giro di capacità (la correzione tocca la scrittura, non la lettura).
#
# Prima di ogni invio lo script controlla che i job in coda più quelli da inviare non superino
# MAX_JOBS (10). Nessuna fase scrive su un'uscita esistente; i log esistenti non si toccano (nella
# fase report un log già presente si salta).
#
# Variabili: JOBS (job per invio, default 2), JOBS_ATTACK (fasi 5, 7, 8; default 4), MAX_JOBS.
set -euo pipefail

PHASE="${1:?usage: run_p2_mcssfix.sh watermark|execute_detect|calibrate|metrics|attack|imperceptibility|attack_execute_detect|attack_metrics|imperceptibility_metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-2}"
JOBS_ATTACK="${JOBS_ATTACK:-4}"
MAX_JOBS="${MAX_JOBS:-10}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)
MM_COPY="${MM_COPY:-/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_multimark}"

# Libreria corretta e hash di mcss (tests/unit/test_posthoc_config_hashes.py).
EXPECTED=3c47a1fee86f4f37535e45aa6ca09787d56881be
LIB=build/patched/multimark
MCSS_NEW=ae63f16de28f
# Configurazioni già calcolate, solo lette dai report.
MCSS=b521002e09f5
MCSS_R2=1d0c64c5124e
MM=3c1bd11758fc
MM_SL=4937d9ddd78a
MM_A=770f999adefb
MM_B=cb79b2fcb4e6
MM_C=cf7e4c9f106e
MM_D=37400fdcd589
LOGS=docs/milestone_logs

ATTACKS="attacks=[T1.1,T1.2,T1.3,T1.4,T1.5,T1.6,T2.1,T2.4,T4.1]"
CLEAN=("methods=[mcss]" "levels=[L1,L2]" "splits=[dev]" "languages=[python]")
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

check_hashes() {
    local mcss
    mcss="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods mcss --only default --expect-commit "$EXPECTED")"
    [ "$mcss" = "$MCSS_NEW" ] || die "mcss default is $mcss, expected $MCSS_NEW"
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
    "${SRUN[@]}" -c 2 --mem 16G -t 180 "$BENCH_PY" "$@" --report "$tmp"
    {
        echo "# $header (scripts/run_p2_mcssfix.sh report): $(date -Iseconds)"
        echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED for mcss $MCSS_NEW; other configs read from existing cells"
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
        room $((JOBS + 1))
        out="$(mktemp -u "$WMB/logs/p2_thresholds_mcssfix_XXXXXX.txt")"
        status=0
        "${SRUN[@]}" -c 1 --mem 8G -t 60 "$BENCH_PY" scripts/compare_thresholds.py \
            --method mcss --old "$MCSS" --new "$MCSS_NEW" --levels L1 L2 > "$out" 2>&1 || status=$?
        cat "$out"
        if [ ! -e "$LOGS/P2_thresholds_mcssfix.txt" ]; then
            {
                echo "# P2 mcss thresholds, old vs new (scripts/run_p2_mcssfix.sh metrics): $(date -Iseconds)"
                echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED"
                cat "$out"
            } > "$out.log"
            mv -n "$out.log" "$LOGS/P2_thresholds_mcssfix.txt"
        fi
        [ "$status" -eq 0 ] || die "thresholds of $MCSS_NEW differ from $MCSS: nothing submitted ($out)"
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
        room $((3 * JOBS))
        submit --jobs "$JOBS" stage=perplexity "${IMP[@]}" "$PPL_SOURCES"
        submit --jobs "$JOBS" stage=adversarial_logreg "${IMP[@]}"
        submit --jobs "$JOBS" stage=adversarial_unixcoder "${IMP[@]}"
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
        room "$JOBS"
        submit --jobs "$JOBS" stage=metrics_imperceptibility "${IMP[@]}"
        ;;
    report)
        done_cells metrics "${ATTACKED[@]}"
        done_cells metrics_imperceptibility "${IMP[@]}"
        room 1
        MM_ALL=(--config "multimark:$MM:multimark" --config "multimark:$MM_SL:single_letter"
                --config "multimark:$MM_A:A" --config "multimark:$MM_B:B"
                --config "multimark:$MM_C:C" --config "multimark:$MM_D:D")
        MCSS_BOTH=(--config "mcss:$MCSS:mcss_old_a4e6972" --config "mcss:$MCSS_NEW:mcss_new_3c47a1f")
        DEV=(--levels L1 L2 --split dev --language python)
        report_log P2_rename_check_mcssfix.txt "P2 rename check of the new mcss" \
            scripts/check_rename_dev.py --config "mcss:$MCSS_NEW:mcss_new_3c47a1f" \
            --multimark "$MM_COPY" "${DEV[@]}"
        report_log P2_round3_mcssfix.txt "P2 capacity round on dev, mcss redone" \
            scripts/report_round3.py "${MCSS_BOTH[@]}" "${MM_ALL[@]}" \
            --curve mcss_old_a4e6972 mcss_new_3c47a1f C "${DEV[@]}"
        report_log P2_snake_mcssfix.txt "P2 T1.6 snake_case on dev, mcss redone" \
            scripts/report_snake_dev.py "${MCSS_BOTH[@]}" "${MM_ALL[@]}" "${DEV[@]}"
        report_log P2_imperceptibility_mcssfix.txt "P2 imperceptibility on dev, mcss redone" \
            scripts/report_imperceptibility_dev.py "${MCSS_BOTH[@]}" "${MM_ALL[@]}" \
            --base multimark --r-point single_letter "${DEV[@]}"
        report_log P2_attacks_dev_mcssfix.txt "P2 attacks on dev, mcss redone vs mcss of round 2" \
            scripts/report_attacks_dev.py --config "mcss:$MCSS_R2:mcss_round2_lib475d4cc" \
            --config "mcss:$MCSS_NEW:mcss_new_3c47a1f" "${DEV[@]}"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
