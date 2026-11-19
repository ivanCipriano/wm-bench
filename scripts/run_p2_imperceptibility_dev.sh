#!/usr/bin/env bash
# Impercettibilità su sviluppo di mcss e multimark (pipeline di multimark v1.3.5; decisioni
# dell'utente del 19 novembre 2026): per chiudere la scelta fra D (37400fdcd589, provvisoria) e C
# (cf7e4c9f106e) serve l'impercettibilità, anticipata a ora su L1 e L2 dev. Celle: mcss
# (b521002e09f5) e i 6 punti di configs/hpo/multimark.yaml (sweep con hpo.grid=multimark, ADR-017
# §7), sui marcati del giro di capacità (libreria a4e6972). Si lancia dal nodo di login:
#
#   bash scripts/run_p2_imperceptibility_dev.sh submit   # 1) perplexity (GPU, StarCoder2-7B:
#                                                        #    marcati e baseline), adversarial_logreg
#                                                        #    (CPU) e adversarial_unixcoder (GPU)
#   bash scripts/run_p2_imperceptibility_dev.sh metrics  # 2) metrics_imperceptibility (CPU)
#   bash scripts/run_p2_imperceptibility_dev.sh report   # 3) docs/milestone_logs/
#                                                        #    P2_imperceptibility_dev.txt
#
# Solo split=dev: nessuna cella di test (le celle L1 test della M12 non si toccano) e nessun
# modello addestrato sul test (i classificatori si addestrano out-of-fold dentro ogni cella dev,
# 5 fold raggruppati per problema). UniXcoder è una misura secondaria; il vincolo di v1.3.3 resta
# su adversarial_logreg (AUROC entro 0,10 da multimark base).
#
# Ogni fase controlla che la precedente sia finita (scripts/check_stage_done.py) e che nessun job
# di quella fase sia in coda; nessuna fase scrive su un'uscita esistente (la pipeline salta le
# celle complete) e il log del report non deve esistere.
#
# Variabili: JOBS (job paralleli per ogni invio, default 1: 6 job nella fase submit, 2 in metrics).
set -euo pipefail

PHASE="${1:?usage: run_p2_imperceptibility_dev.sh submit|metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-1}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)

# Libreria e configurazioni del giro di capacità (scripts/run_p2_round3.sh).
EXPECTED=a4e6972b1ee5cc1027cd4fdadf0fd41f67a67d4c
LIB=build/patched/multimark
MCSS=b521002e09f5
MM=3c1bd11758fc
MM_SL=4937d9ddd78a
MM_A=770f999adefb
MM_B=cb79b2fcb4e6
MM_C=cf7e4c9f106e
MM_D=37400fdcd589
LOG_R3=docs/milestone_logs/P2_round3.txt
LOG=docs/milestone_logs/P2_imperceptibility_dev.txt

COMMON=("levels=[L1,L2]" "splits=[dev]" "languages=[python]" "attacks=[]")
DEFAULT=("methods=[mcss,multimark]" "${COMMON[@]}")
SWEEP=("methods=[multimark]" "hpo.grid=multimark" "${COMMON[@]}")
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
    local mcss mm grid want
    mcss="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods mcss --only default --expect-commit "$EXPECTED")"
    mm="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only default --expect-commit "$EXPECTED")"
    grid="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only grid --expect-commit "$EXPECTED" | sort | paste -sd' ' -)"
    want="$(printf '%s\n' "$MM_SL" "$MM_A" "$MM_B" "$MM_C" "$MM_D" | sort | paste -sd' ' -)"
    [ "$mcss" = "$MCSS" ] || die "mcss default is $mcss, expected $MCSS"
    [ "$mm" = "$MM" ] || die "multimark default is $mm, expected $MM"
    [ "$grid" = "$want" ] || die "multimark dev points are $grid, expected $want"
}

no_jobs() {  # $1 = prefisso del nome dei job ancora in coda o in esecuzione
    local running
    running="$(squeue -h -u "$USER" -o '%i %j' 2>/dev/null | awk -v p="$1" '$2 == p || index($2, p ".") == 1 || index($2, p "-") == 1' || true)"
    [ -z "$running" ] || die "jobs still queued or running for $1: $(echo "$running" | paste -sd' ' -)"
}

stage_done() {  # $1 = fase, $2 = override in più (facoltativo)
    local stage="$1"
    no_jobs "wmb-$stage"
    "$BENCH_PY" scripts/check_stage_done.py "stage=$stage" "${DEFAULT[@]}" ${2:+"$2"} \
        || die "$stage not finished (default cells)"
    "$BENCH_PY" scripts/check_stage_done.py "stage=$stage" "${SWEEP[@]}" ${2:+"$2"} \
        || die "$stage not finished (multimark sweep)"
}

submit() {  # $@ = argomenti di bench submit; stampa gli ID dei job su stdout (ultima riga)
    local out
    out="$(bench submit "$@")" || { echo "$out" >&2; die "bench submit failed"; }
    echo "$out" | grep -v "parameters:" >&2
    echo "$out" | sed -n 's/^SLURM job \([0-9][0-9]*\) .*/\1/p' | paste -sd: -
}

submit_default_then_sweep() {  # $1 = fase; altri argomenti = override in più
    local stage="$1" ids
    shift
    echo "##### $stage: default cells of mcss and multimark" >&2
    ids="$(submit --jobs "$JOBS" "stage=$stage" "${DEFAULT[@]}" "$@")"
    echo "##### $stage: multimark sweep (configs/hpo/multimark.yaml), after ${ids:-none}" >&2
    if [ -n "$ids" ]; then
        submit --jobs "$JOBS" --after "$ids" --allow-concurrent "stage=$stage" "${SWEEP[@]}" "$@" >/dev/null
    else
        submit --jobs "$JOBS" "stage=$stage" "${SWEEP[@]}" "$@" >/dev/null
    fi
}

check_library
check_hashes
case "$PHASE" in
    submit)
        [ -f "$LOG_R3" ] || die "capacity round not finished ($LOG_R3)"
        stage_done watermark
        for stage in perplexity adversarial_logreg adversarial_unixcoder; do no_jobs "wmb-$stage"; done
        submit_default_then_sweep perplexity "$PPL_SOURCES"
        submit_default_then_sweep adversarial_logreg
        submit_default_then_sweep adversarial_unixcoder
        ;;
    metrics)
        stage_done perplexity "$PPL_SOURCES"
        stage_done adversarial_logreg
        stage_done adversarial_unixcoder
        no_jobs wmb-metrics_imperceptibility
        submit_default_then_sweep metrics_imperceptibility
        ;;
    report)
        stage_done metrics_imperceptibility
        [ ! -e "$LOG" ] || die "$LOG already exists"
        mkdir -p "$WMB/logs"
        TMP="$(mktemp -u "$WMB/logs/p2_imperceptibility_dev_XXXXXX.txt")"
        "${SRUN[@]}" -c 2 --mem 16G -t 120 "$BENCH_PY" scripts/report_imperceptibility_dev.py \
            --config "mcss:$MCSS:mcss" --config "multimark:$MM:multimark" \
            --config "multimark:$MM_SL:single_letter" --config "multimark:$MM_A:A" \
            --config "multimark:$MM_B:B" --config "multimark:$MM_C:C" \
            --config "multimark:$MM_D:D" --base multimark --r-point single_letter \
            --levels L1 L2 --split dev --language python --report "$TMP"
        {
            echo "# P2 imperceptibility on dev (scripts/run_p2_imperceptibility_dev.sh report): $(date -Iseconds)"
            echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED"
            cat "$TMP"
        } > "$TMP.log"
        rm -f "$TMP"
        mv -n "$TMP.log" "$LOG"
        [ ! -e "$TMP.log" ] || die "$LOG appeared meanwhile: output left in $TMP.log"
        echo "[OK] $LOG"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
