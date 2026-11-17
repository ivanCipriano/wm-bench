#!/usr/bin/env bash
# Secondo giro di P2 (pipeline di multimark v1.3.0): mcss e multimark con la libreria corretta
# (MultiMark 475d4cc, regole MD20-MD26), senza toccare i risultati del primo giro (libreria
# 488fb5a). Si lancia dal nodo di login, una fase alla volta:
#
#   bash scripts/run_p2_round2.sh inventory       # 1) tabella mcss con le regole nuove, base di
#                                                 #    multimark, selezione 32-39 e dettaglio della
#                                                 #    fascia (job sbatch di inventory_sites.sh)
#   bash scripts/run_p2_round2.sh paper_none      # 2) mcss_paper_attacks.py --attack none sulla
#                                                 #    selezione nuova (criterio (a), srun)
#   bash scripts/run_p2_round2.sh watermark       # 3) watermark L1+L2 dev: default dei due metodi,
#                                                 #    poi lo sweep di multimark (due punti di dev)
#   bash scripts/run_p2_round2.sh execute_detect  # 4) execute (llm_watermarked) e detect
#   bash scripts/run_p2_round2.sh calibrate       # 5) soglie provvisorie L1+L2
#   bash scripts/run_p2_round2.sh metrics         # 6) metriche: default, poi i due punti di
#                                                 #    sviluppo di multimark (ADR-017 §5)
#   bash scripts/run_p2_round2.sh report          # 7) P2_r2_dev.txt: report_metrics.py (con la
#                                                 #    soglia teorica score >= 2) e report_capacity.py
#
# Ogni fase controlla che la precedente sia finita (log scritti, oppure ogni uscita di ogni cella
# presente: scripts/check_stage_done.py) e che nessun job di quella fase sia ancora in coda; se no
# si ferma senza inviare nulla. Le fasi 3-6 inviano job con `bench submit` e tornano subito: la
# fase dopo si lancia quando i job sono finiti (squeue -u $USER).
#
# Perché il secondo giro non tocca il primo: il config_hash contiene il commit della copia
# patchata (build/patched/multimark.source_commit, ADR-003), quindi con la libreria nuova ogni
# cella ha percorsi nuovi in watermarked/, execution/, detection/, thresholds/, metrics_L1L2/ (la
# pipeline salta solo le celle le cui uscite esistono già, e nessuna fase scrive su un'uscita
# esistente). Le uscite di inventario, selezione e capacità sono separate per commit o per hash; i
# log hanno nomi nuovi (P2_r2_*). Per questo ogni fase controlla prima che submodule, copia
# patchata e commit atteso coincidano: con la copia vecchia gli hash sarebbero quelli del primo
# giro e la pipeline salterebbe tutto.
#
# Variabili: JOBS (job paralleli per invio, default 2), ALLOW_CRITERION_A_FAIL=1 (fase watermark
# anche se il criterio (a) del giro 2 fallisce: per la pipeline è un bug, ci si ferma).
set -euo pipefail

PHASE="${1:?usage: run_p2_round2.sh inventory|paper_none|watermark|execute_detect|calibrate|metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
POSTHOC_PY="${POSTHOC_PY:-$(sed -n 's/^  posthoc: *{python: *\([^}]*\)}.*$/\1/p' configs/envs/envs.yaml)}"
JOBS="${JOBS:-2}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)

# Commit della libreria del secondo giro (pipeline v1.3.0). Il primo giro è 488fb5a.
EXPECTED=475d4cc89ca5ef8a8571a0246f4eb4c0d653edf0
C="${EXPECTED:0:12}"
LIB=build/patched/multimark
INV="$WMB/artifacts/_runs/diagnostics/inventory"
SELECTION="$INV/mcss_selftest_r2_$C.jsonl"
RECORDS="$INV/mcss_paper_attacks_none_r2_$C.jsonl"
LOG_INV=docs/milestone_logs/P2_r2_mcss_selftest.txt
LOG_PAPER=docs/milestone_logs/P2_r2_mcss_paper_attacks_none.txt
LOG_DEV=docs/milestone_logs/P2_r2_dev.txt

COMMON=("levels=[L1,L2]" "splits=[dev]" "languages=[python]")
DEFAULT=("methods=[mcss,multimark]" "${COMMON[@]}")
SWEEP=("methods=[multimark]" "hpo.grid=multimark" "${COMMON[@]}")

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

no_jobs() {  # $1 = prefisso del nome dei job ancora in coda o in esecuzione
    local running
    running="$(squeue -h -u "$USER" -o '%i %j' 2>/dev/null | awk -v p="$1" '$2 == p || index($2, p ".") == 1 || index($2, p "-") == 1' || true)"
    [ -z "$running" ] || die "jobs still queued or running for $1: $(echo "$running" | paste -sd' ' -)"
}

stage_done() {  # $1 = fase della pipeline: celle di default e (se ci partecipa) sweep di multimark
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

hashes() {  # $1 = metodo, $2 = default|grid
    "$BENCH_PY" scripts/posthoc_config_hashes.py --methods "$1" --only "$2" --expect-commit "$EXPECTED"
}

check_library
case "$PHASE" in
    inventory)
        [ ! -e "$LOG_INV" ] || die "$LOG_INV already exists: inventory already done"
        no_jobs wmb-inventory-sites
        LOG="$LOG_INV" bash scripts/inventory_sites.sh selftest_r2
        ;;
    paper_none)
        no_jobs wmb-inventory-sites
        [ -f "$LOG_INV" ] && [ -f "$SELECTION" ] || die "inventory not finished ($LOG_INV, $SELECTION)"
        [ ! -e "$LOG_PAPER" ] || die "$LOG_PAPER already exists"
        [ ! -e "$RECORDS" ] || die "$RECORDS already exists"
        "${SRUN[@]}" -c 8 --mem 16G -t 30 "$POSTHOC_PY" scripts/mcss_paper_attacks.py \
            --selection "$SELECTION" --attack none --workers 8 \
            --report "$LOG_PAPER" --records "$RECORDS"
        grep "criterion (a)" "$LOG_PAPER"
        ;;
    watermark)
        [ -f "$LOG_PAPER" ] || die "paper_none not finished ($LOG_PAPER)"
        if grep "criterion (a)" "$LOG_PAPER" | grep -q FAIL && [ "${ALLOW_CRITERION_A_FAIL:-0}" != 1 ]; then
            grep "criterion (a)" "$LOG_PAPER" >&2
            die "criterion (a) fails in round 2: look at the cases (scripts/export_posthoc_failures.py) before going on"
        fi
        echo "##### config_hash of round 2 (scripts/posthoc_config_hashes.py)"
        "$BENCH_PY" scripts/posthoc_config_hashes.py --expect-commit "$EXPECTED"
        submit_default_then_sweep watermark
        ;;
    execute_detect)
        stage_done watermark
        submit_default_then_sweep execute "execution.sources=[llm_watermarked]"
        submit_default_then_sweep detect
        ;;
    calibrate)
        stage_done execute "execution.sources=[llm_watermarked]"
        stage_done detect
        submit_default_then_sweep calibrate
        ;;
    metrics)
        stage_done calibrate
        submit_default_then_sweep metrics
        ;;
    report)
        stage_done metrics
        [ ! -e "$LOG_DEV" ] || die "$LOG_DEV already exists"
        MCSS="$(hashes mcss default)"
        MM="$(hashes multimark default)"
        MM_GRID="$(hashes multimark grid | paste -sd' ' -)"
        TMP="$(mktemp "$WMB/logs/p2_r2_dev_XXXXXX")"
        R=("${SRUN[@]}" -c 2 --mem 16G -t 60)
        {
            echo "# P2 round 2 (scripts/run_p2_round2.sh report): $(date -Iseconds)"
            echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED"
            echo "# configs: mcss $MCSS; multimark $MM; multimark dev points $MM_GRID"
            for L in L1 L2; do
                "${R[@]}" "$BENCH_PY" scripts/report_metrics.py --method mcss --level "$L" --final --config "$MCSS"
            done
            "${R[@]}" "$BENCH_PY" scripts/report_capacity.py --method mcss --config-hash "$MCSS" --levels L1 L2
            for L in L1 L2; do
                # shellcheck disable=SC2086  # MM_GRID: un hash per parola
                "${R[@]}" "$BENCH_PY" scripts/report_metrics.py --method multimark --level "$L" --final --config "$MM" $MM_GRID
            done
            for H in "$MM" $MM_GRID; do
                "${R[@]}" "$BENCH_PY" scripts/report_capacity.py --method multimark --config-hash "$H" --levels L1 L2
            done
        } > "$TMP"
        mv -n "$TMP" "$LOG_DEV"
        [ ! -e "$TMP" ] || die "$LOG_DEV appeared meanwhile: output left in $TMP"
        echo "[OK] $LOG_DEV"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
