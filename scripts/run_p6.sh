#!/usr/bin/env bash
# P6 dei metodi post-hoc (PIPELINE di multimark v1.3.16, decisioni dell'utente del 24 novembre
# 2026; ADR-019): verifiche su dev prima del congelamento e congelamento. La parte di test si
# lancia poi con le catene (python scripts/chains.py loop --owner ivan), i cui passi di mcss e
# multimark restano «held» finché i due metodi non sono congelati.
#
# Configurazioni (configs/freeze/posthoc.yaml, libreria b2b6c18):
#   mcss main 1d3087c73439; multimark prudent e52b040770df, high_capacity 3fed1f1c1e24
#
# Dal nodo di login, una fase alla volta (le fasi con srun dentro tmux):
#
#   bash scripts/run_p6.sh check                # 0) libreria, hash del registro, coda
#   bash scripts/run_p6.sh k2_detect            # 1) rilevazione con k2 su L1+L2 dev (negativi)
#   bash scripts/run_p6.sh aggregated_verify    # 2) verifica dell'aggregata su dev (srun, log)
#   bash scripts/run_p6.sh k2_calibrate         # 3) soglie di k2 su L1+L2 dev (dopo la 1)
#   bash scripts/run_p6.sh dry_run              # 4) controllo a vuoto del test e stima dei costi
#   bash scripts/run_p6.sh aggregated_calibrate # 5) soglie τ_k su dev (dopo l'esame della 2)
#   FREEZE_APPROVED=yes bash scripts/run_p6.sh freeze   # 6) SOLO dopo l'approvazione dell'utente
#
# Le fasi 1 e 2 possono girare insieme. Tutto su CPU. Nessuna fase scrive su un'uscita esistente;
# un log già presente si salta. Le fasi 1–5 non toccano la parte di test (la 4 la pianifica solo
# in sola lettura e controlla che il CellPlanner la rifiuti prima del congelamento).
set -euo pipefail

PHASE="${1:?usage: run_p6.sh check|k2_detect|aggregated_verify|k2_calibrate|dry_run|aggregated_calibrate|freeze}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
BENCH_PY="$WMB/bench-core/bin/python"
JOBS="${JOBS:-2}"
MAX_JOBS="${MAX_JOBS:-10}"
SRUN=(srun -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos)

EXPECTED=b2b6c18ea5303ab134f84f11d8b1de1c2bb5ec5a
LIB=build/patched/multimark
MCSS=1d3087c73439
PRUDENT=e52b040770df
HIGH=3fed1f1c1e24
LOGS=docs/milestone_logs
K2_DEV=("levels=[L1,L2]" "splits=[dev]" "languages=[python]" "attacks=[]")
K2_CAL=("levels=[L1,L2]" "languages=[python]" "attacks=[]")

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

check_hashes() {  # gli hash del registro ricalcolati con la libreria corrente
    local mcss grid
    mcss="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods mcss --only default --expect-commit "$EXPECTED")"
    [ "$mcss" = "$MCSS" ] || die "mcss default is $mcss, expected $MCSS"
    grid="$("$BENCH_PY" scripts/posthoc_config_hashes.py --methods multimark --only grid --expect-commit "$EXPECTED" | paste -sd' ' -)"
    case " $grid " in *" $PRUDENT "*) ;; *) die "prudent $PRUDENT not in the multimark grid ($grid)" ;; esac
    case " $grid " in *" $HIGH "*) ;; *) die "high_capacity $HIGH not in the multimark grid ($grid)" ;; esac
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

finish_log() {  # $1 = nome del log, $2 = intestazione, $3 = uscita temporanea del comando
    local name="$1" header="$2" tmp="$3"
    {
        echo "# $header (scripts/run_p6.sh): $(date -Iseconds)"
        echo "# wm-bench $(git rev-parse HEAD), multimark $EXPECTED"
        cat "$tmp"
    } > "$tmp.log"
    rm -f "$tmp"
    mv -n "$tmp.log" "$LOGS/$name"
    [ ! -e "$tmp.log" ] || die "$LOGS/$name appeared meanwhile: output left in $tmp.log"
    echo "[OK] $LOGS/$name"
}

skip_log() {  # $1 = nome del log: 0 se esiste già (la fase si salta)
    if [ -e "$LOGS/$1" ]; then
        echo "[SKIP] $LOGS/$1 already exists (not overwritten)"
        return 0
    fi
    return 1
}

expect_refused() {  # $1 = descrizione; altri argomenti = bench submit --dry-run (deve fallire)
    local what="$1" out
    shift
    if out="$(bench submit --dry-run "$@" 2>&1)"; then
        echo "FAIL $what: the test split was planned ($*)"
        return 1
    fi
    echo "OK   $what: refused"
    echo "$out" | grep -E "P6 freeze check|not frozen|not allowed|not registered" | head -4 | sed 's/^/       /'
}

check_library
check_hashes
mkdir -p "$WMB/logs"
case "$PHASE" in
    check)
        echo "##### config_hash of the registry (scripts/posthoc_config_hashes.py)"
        "$BENCH_PY" scripts/posthoc_config_hashes.py --methods mcss multimark --expect-commit "$EXPECTED"
        echo "##### queue"
        squeue -u "$USER" || true
        echo "[OK] library $EXPECTED, registry hashes $MCSS $PRUDENT $HIGH"
        ;;
    k2_detect)
        no_jobs wmb-detect_k2
        room $((1 + JOBS))
        submit --jobs 1 stage=detect_k2 "methods=[mcss]" "${K2_DEV[@]}"
        submit --jobs "$JOBS" stage=detect_k2 "methods=[multimark]" "${K2_DEV[@]}"
        ;;
    k2_calibrate)
        done_cells detect_k2 "methods=[mcss]" "${K2_DEV[@]}"
        done_cells detect_k2 "methods=[multimark]" "${K2_DEV[@]}"
        no_jobs wmb-calibrate_k2
        room 2
        submit --jobs 1 stage=calibrate_k2 "methods=[mcss]" "${K2_CAL[@]}"
        submit --jobs 1 stage=calibrate_k2 "methods=[multimark]" "${K2_CAL[@]}"
        ;;
    aggregated_verify)
        name=P6_aggregated_dev.txt
        skip_log "$name" && exit 0
        room 1
        tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
        status=0
        "${SRUN[@]}" -c 2 --mem 16G -t 120 "$BENCH_PY" scripts/report_aggregated.py verify --report "$tmp" || status=$?
        finish_log "$name" "P6 aggregated detection, checks on dev before the freeze" "$tmp"
        [ "$status" -eq 0 ] || die "aggregated checks FAILED: read $LOGS/$name"
        ;;
    dry_run)
        name=P6_test_dry_run.txt
        skip_log "$name" && exit 0
        tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
        status=0
        {
            echo "##### the test split must be refused before the freeze (bench submit --dry-run)"
            for m in mcss multimark; do
                base=("stage=watermark" "methods=[$m]" "levels=[L1]" "splits=[test]" "languages=[python]")
                expect_refused "$m not frozen" "${base[@]}" || status=1
                expect_refused "$m with hpo.grid" "${base[@]}" "hpo.grid=$m" "hpo.use_selected=false" || status=1
                expect_refused "$m with hpo.points" "${base[@]}" "hpo.points=['$PRUDENT']" || status=1
            done
            echo
            for m in mcss multimark; do
                "$BENCH_PY" scripts/check_test.py "$m" --plan || status=1
                echo
            done
            echo "##### cost of the test part (scripts/estimate_closing_cost.py --methods mcss multimark)"
            "$BENCH_PY" scripts/estimate_closing_cost.py --methods mcss multimark || status=1
        } > "$tmp" 2>&1
        finish_log "$name" "P6 dry run of the test split of mcss and multimark (nothing submitted)" "$tmp"
        [ "$status" -eq 0 ] || die "dry run FAILED: read $LOGS/$name"
        ;;
    aggregated_calibrate)
        name=P6_aggregated_calibrate.txt
        skip_log "$name" && exit 0
        [ -e "$LOGS/P6_aggregated_dev.txt" ] || die "run aggregated_verify first"
        room 1
        tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
        "${SRUN[@]}" -c 2 --mem 16G -t 120 "$BENCH_PY" scripts/report_aggregated.py calibrate --report "$tmp"
        finish_log "$name" "P6 aggregated thresholds on L1+L2 dev (to be frozen)" "$tmp"
        ;;
    freeze)
        [ "${FREEZE_APPROVED:-}" = yes ] || die "the freeze needs the approval of the user: FREEZE_APPROVED=yes"
        # come la provenienza: file versionati modificati, esclusi i log delle milestone
        dirty="$(git --no-optional-locks status --porcelain --untracked-files=no | grep -v ' docs/milestone_logs/' || true)"
        [ -z "$dirty" ] || die "uncommitted changes ($dirty): the freeze commit gets the tag p6-freeze"
        done_cells calibrate_k2 "methods=[mcss]" "${K2_CAL[@]}"
        done_cells calibrate_k2 "methods=[multimark]" "${K2_CAL[@]}"
        [ -e "$LOGS/P6_aggregated_calibrate.txt" ] || die "run aggregated_calibrate first"
        for m in mcss multimark; do
            name="P6_freeze_$m.txt"
            skip_log "$name" && continue
            tmp="$(mktemp -u "$WMB/logs/${name%.txt}_XXXXXX.txt")"
            "$BENCH_PY" scripts/freeze_method.py "$m" > "$tmp" || { cat "$tmp"; die "$m not frozen"; }
            finish_log "$name" "P6 freeze of $m" "$tmp"
        done
        no_jobs wmb-freeze_k2
        room 2
        submit --jobs 1 stage=freeze_k2 "methods=[mcss]" "${K2_CAL[@]}"
        submit --jobs 1 stage=freeze_k2 "methods=[multimark]" "${K2_CAL[@]}"
        echo "[OK] frozen. Tags (on the freeze commit; you push them):"
        echo "  git tag -a p6-freeze -m 'P6 freeze of mcss and multimark' $(git rev-parse HEAD)"
        echo "  git -C third_party/multimark tag -a p6-freeze -m 'P6 freeze (wm-bench)' $EXPECTED"
        ;;
    *)
        die "unknown phase $PHASE"
        ;;
esac
