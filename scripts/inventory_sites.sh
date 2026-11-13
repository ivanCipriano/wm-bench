#!/usr/bin/env bash
# Inventario dei siti di multimark (PIPELINE_metodo.md di multimark, P0): job su defq.
# Si lancia dal nodo di login, dopo `bash scripts/apply_patches.sh multimark`. Tre usi:
#
#   LOG=docs/milestone_logs/P0_inventory.txt bash scripts/inventory_sites.sh [VARIANTE ...]
#       1. scripts/export_inventory_inputs.py (bench-core): JSONL di L1 dev (canoniche, baseline
#          dei modelli), L2 dev (canoniche, baseline) e ClassEval (canoniche);
#       2. scripts/inventory_sites.py (posthoc), una volta per variante;
#       3. riepiloghi uniti in $LOG.
#     Varianti (default: base params):
#       base      opzioni di base di multimark (forme `kind`, condizioni attive, parametri esclusi)
#       params    come base con --include-params (decisione C, PIPELINE §7)
#       noguards  come base con --no-guards (siti persi per le condizioni semantiche, MD6)
#       mcss      profilo mcss: --canonical paper --include-params
#
#   LOG=docs/milestone_logs/P0_mcss_selftest.txt bash scripts/inventory_sites.sh selftest
#       selezione CodeNet della verifica di mcss (PIPELINE §4.4 punto 2, decisione dell'utente del
#       13 novembre 2026): pool delle sottomissioni umane Python non usate come negativi di test
#       (export --groups L2_codenet_pool), inventario nei profili base e mcss, poi
#       scripts/select_mcss_selftest.py (fascia 32-39 bit, al più 250 file).
#
#   bash scripts/inventory_sites.sh candidates        # LOG di default: P0_candidates.txt
#       multimark.candidates sugli stessi JSONL dell'inventario. Predisposto soltanto: si ferma
#       con un errore chiaro finché il modulo non è nel submodule e la sua invocazione non è
#       stata collegata qui (TODO(ivan), quando la sessione del repository multimark lo consegna).
#
# $LOG non deve esistere (i log non si sovrascrivono). Uscite in
# $WMB/artifacts/_runs/diagnostics/inventory/; log del job in $WMB/logs/inventory_sites_<job>.log.
# Interprete posthoc da configs/envs/envs.yaml (o POSTHOC_PY=...). Un nuovo job aspetta quelli
# dell'inventario già in coda (scrivono negli stessi ingressi).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
cd "$REPO_ROOT"

MODE=inventory
case "${1:-}" in
    selftest|candidates) MODE="$1"; shift ;;
esac
if [ "$MODE" = candidates ] && [ -z "${LOG:-}" ]; then
    LOG=docs/milestone_logs/P0_candidates.txt
fi

LIB="$REPO_ROOT/build/patched/multimark"
LINK="$(git ls-files --stage -- third_party/multimark | awk '$1 == "160000" {print $2}')"
if [ "$MODE" = candidates ]; then
    if [ ! -f "$LIB/multimark/candidates.py" ] && [ ! -f "$LIB/multimark/candidates/__init__.py" ]; then
        echo "multimark.candidates not found in build/patched/multimark (submodule at ${LINK:0:12})." >&2
        echo "Update third_party/multimark to a commit that contains it, then rerun" >&2
        echo "bash scripts/apply_patches.sh multimark." >&2
        exit 1
    fi
    # TODO(ivan): collegare qui l'invocazione di multimark.candidates (ingressi: i JSONL di
    # $WMB/artifacts/_runs/diagnostics/inventory/inputs; uscita: $LOG) quando la sessione del
    # repository multimark ne fissa l'interfaccia. Finché non è fatto, nessun job parte.
    echo "multimark.candidates is present but its invocation is not wired yet in this script:" >&2
    echo "ask Claude Code in the benchmark to connect it (TODO(ivan))." >&2
    exit 1
fi

if [ -z "${LOG:-}" ]; then
    echo "LOG is required, e.g. LOG=docs/milestone_logs/P0_inventory.txt" >&2
    exit 2
fi
case "$LOG" in /*) ;; *) LOG="$REPO_ROOT/$LOG" ;; esac
if [ -e "$LOG" ]; then
    echo "$LOG already exists: choose a new name (logs are never overwritten)" >&2
    exit 1
fi

POSTHOC_PY="${POSTHOC_PY:-$(sed -n 's/^  posthoc: *{python: *\([^}]*\)}.*$/\1/p' configs/envs/envs.yaml)}"
if [ ! -x "$POSTHOC_PY" ]; then
    echo "posthoc interpreter not found: '$POSTHOC_PY' (configs/envs/envs.yaml)" >&2
    exit 1
fi
if [ ! -f "$LIB/multimark/inventory.py" ] || [ "$(cat "$LIB.source_commit" 2>/dev/null)" != "$LINK" ]; then
    echo "build/patched/multimark missing or stale: run bash scripts/apply_patches.sh multimark" >&2
    exit 1
fi

variant() {  # $1 = variante -> "FLAGS|NOME"
    case "$1" in
        base)     echo "|canonical-kind_guards-on_params-off" ;;
        params)   echo "--include-params|canonical-kind_guards-on_params-on" ;;
        noguards) echo "--no-guards|canonical-kind_guards-off_params-off" ;;
        mcss)     echo "--canonical paper --include-params|canonical-paper_guards-on_params-on" ;;
        *) return 1 ;;
    esac
}

if [ "$MODE" = selftest ]; then
    if [ "$#" -gt 0 ]; then echo "selftest takes no arguments" >&2; exit 2; fi
    VARIANTS=(base mcss)
elif [ "$#" -gt 0 ]; then
    VARIANTS=("$@")
else
    VARIANTS=(base params)
fi
FLAGS=()
NAMES=()
for v in "${VARIANTS[@]}"; do
    if ! spec="$(variant "$v")"; then
        echo "unknown variant '$v' (base, params, noguards, mcss)" >&2
        exit 2
    fi
    FLAGS+=("${spec%%|*}")
    NAMES+=("${spec#*|}")
done

OUT="$WMB/artifacts/_runs/diagnostics/inventory"
BENCH_PY="$WMB/bench-core/bin/python"
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi

if [ "$MODE" = selftest ]; then
    POOL="$OUT/pool/L2_codenet_pool.jsonl"
    EXPORT="$BENCH_PY scripts/export_inventory_inputs.py --out-dir $OUT/pool --groups L2_codenet_pool"
    INPUT="$POOL"
    TABLES="$OUT/pool_tables"
    SUMMARY="$OUT/pool_summary"
else
    EXPORT="$BENCH_PY scripts/export_inventory_inputs.py --out-dir $OUT/inputs"
    INPUT="$OUT/inputs"
    TABLES="$OUT/tables"
    SUMMARY="$OUT/summary"
fi

# Script del job con nome proprio: due lanci di fila non si sovrascrivono il file.
JOB="$(mktemp "$WMB/logs/inventory_sites_sbatch_XXXXXX")"
# Un job dell'inventario già in coda o in esecuzione scrive negli stessi ingressi: il nuovo
# parte solo dopo (i due job 818687 e 818688 del 13 novembre 2026 erano partiti insieme).
PREV="$(squeue -h -u "$USER" -n wmb-inventory-sites -o %i 2>/dev/null | paste -sd: -)"
DEP=()
if [ -n "$PREV" ]; then
    DEP=(--dependency "afterany:$PREV")
    echo "waiting for running inventory job(s): $PREV"
fi
{
    cat <<SBATCH
#!/bin/bash -l
set -euo pipefail
set -o noclobber
umask 002
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 PYTHONHASHSEED=0
cd $REPO_ROOT
echo "##### inventory_sites $MODE: nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
$EXPORT
SBATCH
    for i in "${!VARIANTS[@]}"; do
        echo "$POSTHOC_PY scripts/inventory_sites.py --input $INPUT --out-dir $TABLES ${FLAGS[$i]} --summary $SUMMARY.${NAMES[$i]}.txt --summary-json $SUMMARY.${NAMES[$i]}.json"
    done
    if [ "$MODE" = selftest ]; then
        echo "$POSTHOC_PY scripts/select_mcss_selftest.py --pool $POOL --mcss-table $TABLES/L2_codenet_pool.canonical-paper_guards-on_params-on.csv --base-table $TABLES/L2_codenet_pool.canonical-kind_guards-on_params-off.csv --out $OUT/mcss_selftest.jsonl --report $OUT/mcss_selftest_report.txt"
    fi
    echo "{"
    echo "  echo \"# P0 $MODE of multimark sites (scripts/inventory_sites.sh $([ "$MODE" = selftest ] && echo selftest || echo "${VARIANTS[*]}"))\""
    echo "  echo \"# date \$(date -Iseconds), host \$(hostname), wm-bench \$(git rev-parse HEAD)\""
    echo "  echo \"# inputs: $INPUT (<gruppo>.jsonl.manifest.json)\""
    if [ "$MODE" = selftest ]; then
        echo "  echo \"# selection: $OUT/mcss_selftest.jsonl\"; echo"
        echo "  cat $OUT/mcss_selftest_report.txt"
    fi
    for i in "${!VARIANTS[@]}"; do
        echo "  echo; echo \"######## variant ${VARIANTS[$i]} (${NAMES[$i]})\"; echo"
        echo "  cat $SUMMARY.${NAMES[$i]}.txt"
    done
    echo "} > $LOG"
    echo "echo \"[OK] $LOG\""
} > "$JOB"

ID=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
    -c 4 --mem 16G -t 60 ${EXCLUDE[@]+"${EXCLUDE[@]}"} ${DEP[@]+"${DEP[@]}"} \
    --job-name wmb-inventory-sites \
    -o "$WMB/logs/inventory_sites_%j.log" "$JOB")
echo "job $ID (defq): log $WMB/logs/inventory_sites_$ID.log -> $LOG"
