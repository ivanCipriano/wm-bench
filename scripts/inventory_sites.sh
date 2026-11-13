#!/usr/bin/env bash
# Inventario dei siti di multimark (PIPELINE_metodo.md di multimark, P0) in un job su defq:
#   1. scripts/export_inventory_inputs.py (bench-core): JSONL di L1 dev (canoniche, baseline dei
#      modelli), L2 dev (canoniche, baseline) e ClassEval (canoniche);
#   2. scripts/inventory_sites.py (ambiente posthoc), una volta per variante delle opzioni;
#   3. riepiloghi uniti in $LOG (che non deve esistere: i log non si sovrascrivono).
#
# Si lancia dal nodo di login, dopo `bash scripts/apply_patches.sh multimark`:
#
#   LOG=docs/milestone_logs/P0_inventory.txt bash scripts/inventory_sites.sh [VARIANTE ...]
#
# Varianti (default: base params):
#   base      opzioni di base di multimark (forme `kind`, condizioni attive, parametri esclusi)
#   params    come base con --include-params (decisione C, PIPELINE §7)
#   noguards  come base con --no-guards (siti persi per le condizioni semantiche, MD6)
#   mcss      profilo mcss: --canonical paper --include-params
# Uscite in $WMB/artifacts/_runs/diagnostics/inventory/{inputs,tables}/ e summary.*.txt|json;
# log del job in $WMB/logs/inventory_sites_<job>.log. Interprete posthoc da configs/envs/envs.yaml
# (o POSTHOC_PY=...).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
cd "$REPO_ROOT"

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
LIB="$REPO_ROOT/build/patched/multimark"
LINK="$(git ls-files --stage -- third_party/multimark | awk '$1 == "160000" {print $2}')"
if [ ! -f "$LIB/multimark/inventory.py" ] || [ "$(cat "$LIB.source_commit" 2>/dev/null)" != "$LINK" ]; then
    echo "build/patched/multimark missing or stale: run bash scripts/apply_patches.sh multimark" >&2
    exit 1
fi

if [ "$#" -gt 0 ]; then VARIANTS=("$@"); else VARIANTS=(base params); fi
FLAGS=()
NAMES=()
for v in "${VARIANTS[@]}"; do
    case "$v" in
        base)     FLAGS+=("");                                  NAMES+=(canonical-kind_guards-on_params-off) ;;
        params)   FLAGS+=("--include-params");                  NAMES+=(canonical-kind_guards-on_params-on) ;;
        noguards) FLAGS+=("--no-guards");                       NAMES+=(canonical-kind_guards-off_params-off) ;;
        mcss)     FLAGS+=("--canonical paper --include-params"); NAMES+=(canonical-paper_guards-on_params-on) ;;
        *) echo "unknown variant '$v' (base, params, noguards, mcss)" >&2; exit 2 ;;
    esac
done

OUT="$WMB/artifacts/_runs/diagnostics/inventory"
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi

JOB="$WMB/logs/inventory_sites.sbatch"
{
    cat <<SBATCH
#!/bin/bash -l
set -euo pipefail
set -o noclobber
umask 002
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 PYTHONHASHSEED=0
cd $REPO_ROOT
echo "##### inventory_sites: nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
$WMB/bench-core/bin/python scripts/export_inventory_inputs.py --out-dir $OUT/inputs
SBATCH
    for i in "${!VARIANTS[@]}"; do
        echo "$POSTHOC_PY scripts/inventory_sites.py --input $OUT/inputs --out-dir $OUT/tables ${FLAGS[$i]} --summary $OUT/summary.${NAMES[$i]}.txt --summary-json $OUT/summary.${NAMES[$i]}.json"
    done
    echo "{"
    echo "  echo \"# P0 inventory of multimark sites (scripts/inventory_sites.sh ${VARIANTS[*]})\""
    echo "  echo \"# date \$(date -Iseconds), host \$(hostname), wm-bench \$(git rev-parse HEAD)\""
    echo "  echo \"# inputs: $OUT/inputs (<gruppo>.jsonl.manifest.json)\""
    for i in "${!VARIANTS[@]}"; do
        echo "  echo; echo \"######## variant ${VARIANTS[$i]} (${NAMES[$i]})\"; echo"
        echo "  cat $OUT/summary.${NAMES[$i]}.txt"
    done
    echo "} > $LOG"
    echo "echo \"[OK] $LOG\""
} > "$JOB"

ID=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
    -c 4 --mem 16G -t 60 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-inventory-sites \
    -o "$WMB/logs/inventory_sites_%j.log" "$JOB")
echo "job $ID (defq): log $WMB/logs/inventory_sites_$ID.log -> $LOG"
