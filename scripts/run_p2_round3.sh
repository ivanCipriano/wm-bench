#!/usr/bin/env bash
# Giro di capacità su sviluppo (pipeline di multimark v1.3.3, criterio di scelta fissato prima del
# giro; decisioni dell'utente del 18 novembre 2026). Libreria MultiMark a4e6972: con il commit
# nuovo cambiano tutti i config_hash, quindi si rifanno anche mcss e multimark base (i risultati a
# 475d4cc restano). Configurazioni (scripts/posthoc_config_hashes.py, configs/hpo/multimark.yaml):
#
#   mcss b521002e09f5; multimark base 3c1bd11758fc; single_letter=extend_except_loop 4937d9ddd78a;
#   A = base + canonical.same_kind=binding_order 770f999adefb; B = base + names.bits_per_name=2
#   cb79b2fcb4e6; C = base + single_letter + binding_order + bits_per_name=2 cf7e4c9f106e;
#   D = C + names.include_params=true 37400fdcd589.
#
# Si lancia dal nodo di login, una fase alla volta:
#
#   bash scripts/run_p2_round3.sh watermark              # 1) marcati L1+L2 dev: default di mcss e
#                                                        #    multimark, poi lo sweep di multimark
#   bash scripts/run_p2_round3.sh execute_detect         # 2) Pass@1 dei marcati e rilevazione
#   bash scripts/run_p2_round3.sh calibrate              # 3) soglie L1+L2 (nuove: hash nuovi)
#   bash scripts/run_p2_round3.sh metrics                # 4) metriche senza attacco
#   bash scripts/run_p2_round3.sh attack                 # 5) attacchi dei marcati
#   bash scripts/run_p2_round3.sh attack_execute_detect  # 6) Pass@1 degli attaccati e rilevazione
#   bash scripts/run_p2_round3.sh attack_metrics         # 7) metriche delle celle attaccate
#   bash scripts/run_p2_round3.sh report                 # 8) docs/milestone_logs/P2_round3.txt
#                                                        #    (scripts/report_round3.py)
#
# Attacchi: insieme S di v1.3.3 = T1.4 natural r=1,0; T1.4 random r=0,5; T1.5 k=5; T2.1 b=all;
# T2.4 p=0,5; T4.1 rename=true. La pipeline non sceglie singoli punti di una griglia: si lanciano
# le griglie intere di T1.4, T1.5, T2.1, T2.4 e T4.1 (19 punti su Python) e il report usa solo i
# punti di S. Le celle attaccate dei punti di sviluppo entrano nello sweep (ADR-017 §6).
#
# Solo split=dev, solo Python, L1 e L2. Ogni fase controlla che la precedente sia finita
# (scripts/check_stage_done.py: ogni uscita di ogni cella presente) e che nessun job di quella fase
# sia in coda; se no si ferma senza inviare nulla. Le fasi 1-7 inviano job con `bench submit` e
# tornano subito. Nessuna fase scrive su un'uscita esistente: la pipeline salta le celle già
# complete, e il log del report non deve esistere. Ogni fase controlla anche submodule, copia
# patchata e hash: con la copia vecchia gli hash sarebbero diversi e le celle non si troverebbero.
#
# Variabili: JOBS (job paralleli per invio, default 2).
set -euo pipefail

PHASE="${1:?usage: run_p2_round3.sh watermark|execute_detect|calibrate|metrics|attack|attack_execute_detect|attack_metrics|report}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-2}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)

# Libreria e configurazioni del giro di capacità (tests/unit/test_posthoc_config_hashes.py).
EXPECTED=a4e6972b1ee5cc1027cd4fdadf0fd41f67a67d4c
LIB=build/patched/multimark
MCSS=b521002e09f5
MM=3c1bd11758fc
MM_SL=4937d9ddd78a
MM_A=770f999adefb
MM_B=cb79b2fcb4e6
MM_C=cf7e4c9f106e
MM_D=37400fdcd589
LOG=docs/milestone_logs/P2_round3.txt

ATTACKS="attacks=[T1.4,T1.5,T2.1,T2.4,T4.1]"
COMMON=("levels=[L1,L2]" "splits=[dev]" "languages=[python]")
# celle senza attacco
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

check_hashes() {  # le configurazioni calcolate devono essere quelle scritte qui sopra
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

submit_default_then_sweep() {  # $1 = fase, $2 = clean|attacked; altri argomenti = override in più
    local stage="$1" kind="$2" ids
    shift 2
    local -a default=("${DEFAULT[@]}") sweep=("${SWEEP[@]}")
    if [ "$kind" = attacked ]; then
        default=("${A_DEFAULT[@]}")
        sweep=("${A_SWEEP[@]}")
    fi
    echo "##### $stage ($kind): default cells of mcss and multimark" >&2
    ids="$(submit --jobs "$JOBS" "stage=$stage" "${default[@]}" "$@")"
    echo "##### $stage ($kind): multimark sweep (configs/hpo/multimark.yaml), after ${ids:-none}" >&2
    if [ -n "$ids" ]; then
        submit --jobs "$JOBS" --after "$ids" --allow-concurrent "stage=$stage" "${sweep[@]}" "$@" >/dev/null
    else
        submit --jobs "$JOBS" "stage=$stage" "${sweep[@]}" "$@" >/dev/null
    fi
}

check_library
case "$PHASE" in
    watermark)
        check_hashes
        echo "##### config_hash of the capacity round (scripts/posthoc_config_hashes.py)"
        "$BENCH_PY" scripts/posthoc_config_hashes.py --expect-commit "$EXPECTED"
        no_jobs wmb-watermark
        submit_default_then_sweep watermark clean
        ;;
    execute_detect)
        stage_done watermark clean
        submit_default_then_sweep execute clean "execution.sources=[llm_watermarked]"
        submit_default_then_sweep detect clean
        ;;
    calibrate)
        stage_done execute clean "execution.sources=[llm_watermarked]"
        stage_done detect clean
        submit_default_then_sweep calibrate clean
        ;;
    metrics)
        stage_done calibrate clean
        submit_default_then_sweep metrics clean
        ;;
    attack)
        stage_done metrics clean
        done_cells attack_pools "comment and name pools" "languages=[python]"
        no_jobs wmb-attack
        submit_default_then_sweep attack attacked
        ;;
    attack_execute_detect)
        stage_done attack attacked
        submit_default_then_sweep execute attacked "execution.sources=[attacked]"
        submit_default_then_sweep detect attacked
        ;;
    attack_metrics)
        stage_done execute attacked "execution.sources=[attacked]"
        stage_done detect attacked
        submit_default_then_sweep metrics attacked
        ;;
    report)
        stage_done metrics attacked
        [ ! -e "$LOG" ] || die "$LOG already exists"
        check_hashes
        mkdir -p "$WMB/logs"
        TMP="$(mktemp -u "$WMB/logs/p2_round3_XXXXXX.txt")"
        "${SRUN[@]}" -c 2 --mem 16G -t 180 "$BENCH_PY" scripts/report_round3.py \
            --config "mcss:$MCSS:mcss" --config "multimark:$MM:multimark" \
            --config "multimark:$MM_SL:single_letter" --config "multimark:$MM_A:A" \
            --config "multimark:$MM_B:B" --config "multimark:$MM_C:C" \
            --config "multimark:$MM_D:D" --curve mcss multimark C \
            --levels L1 L2 --split dev --language python --report "$TMP"
        {
            echo "# P2 capacity round on dev (scripts/run_p2_round3.sh report): $(date -Iseconds)"
            echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED, $ATTACKS (S points only)"
            cat "$TMP"
        } > "$TMP.log"
        rm -f "$TMP"
        mv -n "$TMP.log" "$LOG"
        [ ! -e "$TMP.log" ] || die "$LOG appeared meanwhile: output left in $TMP.log"
        echo "[OK] $LOG"
        ;;
    *) die "unknown phase: $PHASE" ;;
esac
