#!/usr/bin/env bash
# Campagna chiusa (log scritti). Da a4e6972 l'adapter ha due iperparametri in più e gli hash qui
# sotto non si ricalcolano: per rilanciarla serve wm-bench 48df7da (giro di capacità:
# scripts/run_p2_round3.sh).
# Controllo di robustezza su sviluppo prima di P3 (decisioni dell'utente del 18 novembre 2026):
# attacchi sul codice marcato del secondo giro di P2 (libreria 475d4cc) per mcss (1d0c64c5124e),
# multimark (59a04735824c) e i due punti di sviluppo di multimark (94847adacd77 single_letter
# extend_except_loop, acac071d50d5 include_params true). Si lancia dal nodo di login, una fase alla
# volta:
#
#   bash scripts/run_p2_attacks_dev.sh attack          # 1) attacchi: celle di default di mcss e
#                                                      #    multimark, poi lo sweep di multimark
#                                                      #    (ADR-017 §6)
#   bash scripts/run_p2_attacks_dev.sh execute_detect  # 2) esecuzione degli attaccati (Pass@1) e
#                                                      #    rilevazione
#   bash scripts/run_p2_attacks_dev.sh metrics         # 3) metriche delle celle attaccate
#   bash scripts/run_p2_attacks_dev.sh report          # 4) docs/milestone_logs/P2_attacks_dev.txt
#                                                      #    (scripts/report_attacks_dev.py)
#
# Attacchi: griglie intere di T1.1, T1.2, T1.3, T1.4, T1.5, T2.1, T2.4 e T4.1 (23 punti su
# Python; la pipeline non sceglie singoli punti di una griglia). Il report mette prima i 17 punti
# scelti, poi gli altri 6 (T1.4 r = 0,25 e 0,75, T2.1 b = 2 e 3). T2.2 si aggiunge dopo.
#
# Solo split=dev, solo Python, L1 e L2. Nessuna nuova calibrazione: la fase metrics delle celle
# attaccate usa le soglie del secondo giro (thresholds/.../L1L2/<hash>.json, levels=[L1,L2]); il
# report aggiunge la soglia teorica score >= 2. Il submodule resta a 475d4cc per tutta la campagna
# (la v1.3.1 cambia solo il documento della pipeline): con un altro commit gli hash sarebbero
# diversi e le celle marcate non si troverebbero, quindi ogni fase lo controlla.
#
# Ogni fase controlla che la precedente sia finita (scripts/check_stage_done.py: ogni uscita di
# ogni cella presente) e che nessun job di quella fase sia in coda; se no si ferma senza inviare
# nulla. Le fasi 1-3 inviano job con `bench submit` e tornano subito. Nessuna fase scrive su
# un'uscita esistente: la pipeline salta le celle già complete, e il log del report non deve
# esistere.
#
# Variabili: JOBS (job paralleli per invio, default 2).
set -euo pipefail

PHASE="${1:?usage: run_p2_attacks_dev.sh attack|execute_detect|metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-2}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)

# Libreria e configurazioni del secondo giro di P2.
EXPECTED=475d4cc89ca5ef8a8571a0246f4eb4c0d653edf0
LIB=build/patched/multimark
MCSS=1d0c64c5124e
MM=59a04735824c
MM_SINGLE_LETTER=94847adacd77
MM_PARAMS=acac071d50d5
LOG_R2=docs/milestone_logs/P2_r2_dev.txt
LOG=docs/milestone_logs/P2_attacks_dev.txt

ATTACKS="attacks=[T1.1,T1.2,T1.3,T1.4,T1.5,T2.1,T2.4,T4.1]"
COMMON=("levels=[L1,L2]" "splits=[dev]" "languages=[python]")
DEFAULT=("methods=[mcss,multimark]" "${COMMON[@]}" "$ATTACKS")
SWEEP=("methods=[multimark]" "hpo.grid=multimark" "${COMMON[@]}" "$ATTACKS")
# celle senza attacco del secondo giro (soglie, marcati, esecuzione dei marcati)
R2_DEFAULT=("methods=[mcss,multimark]" "${COMMON[@]}")
R2_SWEEP=("methods=[multimark]" "hpo.grid=multimark" "${COMMON[@]}")

die() { echo "[STOP] $*" >&2; exit 1; }

check_library() {
    local link head copy
    link="$(git ls-files --stage -- third_party/multimark | awk '$1 == "160000" {print $2}')"
    head="$(git -C third_party/multimark rev-parse HEAD 2>/dev/null || echo none)"
    copy="$(cat "$LIB.source_commit" 2>/dev/null || echo none)"
    [ "$link" = "$EXPECTED" ] || die "submodule recorded at $link, expected $EXPECTED (this campaign stays on 475d4cc)"
    [ "$head" = "$EXPECTED" ] || die "third_party/multimark checked out at $head: run git submodule update --init third_party/multimark"
    [ "$copy" = "$EXPECTED" ] || die "$LIB at $copy: run bash scripts/apply_patches.sh multimark"
}

check_hashes() {  # le configurazioni calcolate devono essere quelle del secondo giro
    local mcss mm grid
    mcss="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods mcss --only default --expect-commit "$EXPECTED")"
    mm="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only default --expect-commit "$EXPECTED")"
    grid="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only grid --expect-commit "$EXPECTED" | sort | paste -sd' ' -)"
    [ "$mcss" = "$MCSS" ] || die "mcss default is $mcss, expected $MCSS"
    [ "$mm" = "$MM" ] || die "multimark default is $mm, expected $MM"
    [ "$grid" = "$(printf '%s\n' "$MM_SINGLE_LETTER" "$MM_PARAMS" | sort | paste -sd' ' -)" ] \
        || die "multimark dev points are $grid, expected $MM_SINGLE_LETTER $MM_PARAMS"
}

no_jobs() {  # $1 = prefisso del nome dei job ancora in coda o in esecuzione
    local running
    running="$(squeue -h -u "$USER" -o '%i %j' 2>/dev/null | awk -v p="$1" '$2 == p || index($2, p ".") == 1 || index($2, p "-") == 1' || true)"
    [ -z "$running" ] || die "jobs still queued or running for $1: $(echo "$running" | paste -sd' ' -)"
}

done_cells() {  # $1 = fase, $2 = descrizione; altri argomenti = override di check_stage_done
    local stage="$1" what="$2"
    shift 2
    "$BENCH_PY" scripts/check_stage_done.py "stage=$stage" "$@" || die "$stage not finished ($what)"
}

stage_done() {  # $1 = fase della campagna: celle attaccate di default e dello sweep
    local stage="$1"
    no_jobs "wmb-$stage"
    done_cells "$stage" "default attacked cells" "${DEFAULT[@]}" ${2:+"$2"}
    done_cells "$stage" "multimark sweep, attacked cells" "${SWEEP[@]}" ${2:+"$2"}
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
    echo "##### $stage: default attacked cells of mcss and multimark" >&2
    ids="$(submit --jobs "$JOBS" "stage=$stage" "${DEFAULT[@]}" "$@")"
    echo "##### $stage: multimark sweep (configs/hpo/multimark.yaml), after ${ids:-none}" >&2
    if [ -n "$ids" ]; then
        submit --jobs "$JOBS" --after "$ids" --allow-concurrent "stage=$stage" "${SWEEP[@]}" "$@" >/dev/null
    else
        submit --jobs "$JOBS" "stage=$stage" "${SWEEP[@]}" "$@" >/dev/null
    fi
}

check_library
case "$PHASE" in
    attack)
        [ -f "$LOG_R2" ] || die "round 2 of P2 not finished ($LOG_R2)"
        check_hashes
        # ingressi: marcati, esecuzione dei marcati e soglie del secondo giro; raccolte di T1.3 e T1.4
        for stage in watermark calibrate metrics; do
            done_cells "$stage" "round 2, default cells" "${R2_DEFAULT[@]}"
            done_cells "$stage" "round 2, multimark sweep" "${R2_SWEEP[@]}"
        done
        done_cells attack_pools "comment and name pools" "languages=[python]"
        no_jobs wmb-attack
        submit_default_then_sweep attack
        ;;
    execute_detect)
        stage_done attack
        submit_default_then_sweep execute "execution.sources=[attacked]"
        submit_default_then_sweep detect
        ;;
    metrics)
        stage_done execute "execution.sources=[attacked]"
        stage_done detect
        submit_default_then_sweep metrics
        ;;
    report)
        stage_done metrics
        [ ! -e "$LOG" ] || die "$LOG already exists"
        check_hashes
        mkdir -p "$WMB/logs"
        TMP="$(mktemp -u "$WMB/logs/p2_attacks_dev_XXXXXX.txt")"
        "${SRUN[@]}" -c 2 --mem 16G -t 120 "$BENCH_PY" scripts/report_attacks_dev.py \
            --config "mcss:$MCSS:mcss" --config "multimark:$MM:multimark" \
            --config "multimark:$MM_SINGLE_LETTER:mm_single_letter" \
            --config "multimark:$MM_PARAMS:mm_params" \
            --levels L1 L2 --split dev --language python --report "$TMP"
        {
            echo "# P2 attacks on dev (scripts/run_p2_attacks_dev.sh report): $(date -Iseconds)"
            echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED, $ATTACKS"
            cat "$TMP"
        } > "$TMP.log"
        rm -f "$TMP"
        mv -n "$TMP.log" "$LOG"
        [ ! -e "$TMP.log" ] || die "$LOG appeared meanwhile: output left in $TMP.log"
        echo "[OK] $LOG"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
