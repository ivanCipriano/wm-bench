#!/usr/bin/env bash
# T1.6 nomi in snake_case su sviluppo (ADR-018; decisioni dell'utente del 19 novembre 2026), sul
# codice marcato del giro di capacità (libreria a4e6972) delle stesse 7 configurazioni:
#
#   mcss b521002e09f5; multimark base 3c1bd11758fc; single_letter 4937d9ddd78a; A 770f999adefb;
#   B cb79b2fcb4e6 (provvisoria); C cf7e4c9f106e; D 37400fdcd589.
#
# Due punti dell'attacco, caps=convert e caps=keep (configs/attack/t1_6_snake_case.yaml), in righe
# separate nel report. Nessuna nuova calibrazione: soglie L1+L2 del giro di capacità, più la soglia
# teorica score >= 2. Le celle attaccate dei punti di sviluppo entrano nello sweep (ADR-017 §6).
# Si lancia dal nodo di login, una fase alla volta:
#
#   bash scripts/run_p2_snake_dev.sh attack                 # 1) T1.6 sui marcati
#   bash scripts/run_p2_snake_dev.sh attack_execute_detect  # 2) Pass@1 degli attaccati, rilevazione
#   bash scripts/run_p2_snake_dev.sh attack_metrics         # 3) metriche delle celle attaccate
#   bash scripts/run_p2_snake_dev.sh report                 # 4) docs/milestone_logs/
#                                                           #    P2_snake_dev.txt
#
# Solo split=dev, solo Python, L1 e L2: nessuna cella di test. Ogni fase controlla che la
# precedente sia finita (scripts/check_stage_done.py) e che nessun job di quella fase sia in coda;
# nessuna fase scrive su un'uscita esistente (la pipeline salta le celle complete) e il log del
# report non deve esistere. Le celle degli altri attacchi non si toccano (attacks=[T1.6]).
#
# Variabili: JOBS (job paralleli per invio, default 2: al più 8 job in coda, nella fase 2).
set -euo pipefail

PHASE="${1:?usage: run_p2_snake_dev.sh attack|attack_execute_detect|attack_metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-2}"
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
LOG=docs/milestone_logs/P2_snake_dev.txt

ATTACKS="attacks=[T1.6]"
COMMON=("levels=[L1,L2]" "splits=[dev]" "languages=[python]")
# celle senza attacco (devono esserci: giro di capacità)
DEFAULT=("methods=[mcss,multimark]" "${COMMON[@]}")
SWEEP=("methods=[multimark]" "hpo.grid=multimark" "${COMMON[@]}")
# celle attaccate
A_DEFAULT=("${DEFAULT[@]}" "$ATTACKS")
A_SWEEP=("${SWEEP[@]}" "$ATTACKS")

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

done_cells() {  # $1 = fase, $2 = descrizione; altri argomenti = override di check_stage_done
    local stage="$1" what="$2"
    shift 2
    "$BENCH_PY" scripts/check_stage_done.py "stage=$stage" "$@" || die "$stage not finished ($what)"
}

stage_done() {  # $1 = fase, $2 = clean|attacked, $3 = override in più (facoltativo)
    local stage="$1"
    no_jobs "wmb-$stage"
    if [ "$2" = attacked ]; then
        done_cells "$stage" "default attacked cells" "${A_DEFAULT[@]}" ${3:+"$3"}
        done_cells "$stage" "multimark sweep, attacked cells" "${A_SWEEP[@]}" ${3:+"$3"}
    else
        done_cells "$stage" "default cells" "${DEFAULT[@]}" ${3:+"$3"}
        done_cells "$stage" "multimark sweep" "${SWEEP[@]}" ${3:+"$3"}
    fi
}

submit() {  # $@ = argomenti di bench submit; stampa gli ID dei job su stdout (ultima riga)
    local out
    out="$(bench submit "$@")" || { echo "$out" >&2; die "bench submit failed"; }
    echo "$out" | grep -v "parameters:" >&2
    echo "$out" | sed -n 's/^SLURM job \([0-9][0-9]*\) .*/\1/p' | paste -sd: -
}

submit_default_then_sweep() {  # $1 = fase; altri argomenti = override in più (celle attaccate)
    local stage="$1" ids
    shift
    echo "##### $stage (T1.6): default cells of mcss and multimark" >&2
    ids="$(submit --jobs "$JOBS" "stage=$stage" "${A_DEFAULT[@]}" "$@")"
    echo "##### $stage (T1.6): multimark sweep (configs/hpo/multimark.yaml), after ${ids:-none}" >&2
    if [ -n "$ids" ]; then
        submit --jobs "$JOBS" --after "$ids" --allow-concurrent "stage=$stage" "${A_SWEEP[@]}" "$@" >/dev/null
    else
        submit --jobs "$JOBS" "stage=$stage" "${A_SWEEP[@]}" "$@" >/dev/null
    fi
}

check_library
check_hashes
case "$PHASE" in
    attack)
        stage_done metrics clean
        no_jobs wmb-attack
        submit_default_then_sweep attack
        ;;
    attack_execute_detect)
        stage_done attack attacked
        submit_default_then_sweep execute "execution.sources=[attacked]"
        submit_default_then_sweep detect
        ;;
    attack_metrics)
        stage_done execute attacked "execution.sources=[attacked]"
        stage_done detect attacked
        submit_default_then_sweep metrics
        ;;
    report)
        stage_done metrics attacked
        [ ! -e "$LOG" ] || die "$LOG already exists"
        mkdir -p "$WMB/logs"
        TMP="$(mktemp -u "$WMB/logs/p2_snake_dev_XXXXXX.txt")"
        "${SRUN[@]}" -c 2 --mem 16G -t 120 "$BENCH_PY" scripts/report_snake_dev.py \
            --config "mcss:$MCSS:mcss" --config "multimark:$MM:multimark" \
            --config "multimark:$MM_SL:single_letter" --config "multimark:$MM_A:A" \
            --config "multimark:$MM_B:B" --config "multimark:$MM_C:C" \
            --config "multimark:$MM_D:D" \
            --levels L1 L2 --split dev --language python --report "$TMP"
        {
            echo "# P2 T1.6 snake_case on dev (scripts/run_p2_snake_dev.sh report): $(date -Iseconds)"
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
