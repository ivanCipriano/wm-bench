#!/usr/bin/env bash
# Inventario dei siti di multimark (PIPELINE_metodo.md di multimark, P0): job su defq.
# Si lancia dal nodo di login, dopo `bash scripts/apply_patches.sh multimark`. Tre usi:
#
#   LOG=docs/milestone_logs/P0_inventory.txt bash scripts/inventory_sites.sh [VARIANTE ...]
#       1. scripts/export_inventory_inputs.py (bench-core): JSONL di L1 dev (canoniche, baseline
#          dei modelli), L2 dev (canoniche, baseline) e ClassEval (canoniche);
#       2. scripts/inventory_sites.py (posthoc), una volta per variante;
#       3. riepiloghi uniti in $LOG.
#     Varianti (default: base base_mod mcss mcss_mod); --channels sempre esplicito, perché dal
#     commit P1 di multimark il default della libreria è di 14 canali:
#       base      base di multimark: forme `kind`, condizioni attive, parametri esclusi,
#                 --channels extended (canali 1-14)
#       base_mod  come base con --module-level on (variabili di modulo, decisione O di §7)
#       mcss      profilo mcss: --canonical paper --include-params --channels mcss8 (canali 1-8)
#       mcss_mod  come mcss con --module-level on (profilo della Tabella 3, PIPELINE v1.2 §4.4)
#       params    come base con --include-params (decisione C, PIPELINE §7)
#       noguards  come base con --no-guards (siti persi per le condizioni semantiche, MD6)
#
#   LOG=docs/milestone_logs/P1_mcss_selftest_modlevel.txt bash scripts/inventory_sites.sh selftest
#       selezione CodeNet della verifica di mcss (PIPELINE v1.2 §4.4 punto 2): pool delle
#       sottomissioni umane Python, tranne negativi e canoniche dei problemi di test (export
#       --groups L2_codenet_pool, con L2_codenet_pool.test_clusters.json), inventario nelle
#       varianti base e mcss_mod, poi scripts/select_mcss_selftest.py sulla tabella mcss_mod
#       (fascia 32-39 bit o >= 32, 100-250 file, una sottomissione per problema quando bastano).
#
#   bash scripts/inventory_sites.sh candidates        # LOG di default: P0_candidates.txt
#       canali candidati (P0-bis): `python -m multimark.cli.candidates` sugli stessi JSONL
#       dell'inventario (tutti i gruppi di inputs/: L1 dev canoniche e baseline dei modelli, L2
#       dev canoniche e baseline, ClassEval), con --estimate-groups sui gruppi L1_dev_*. L'export
#       si rifà prima (stessi file: [SKIP]). Uscite per job in candidates/ (summary_<job>.txt,
#       .json e tabella per campione samples_<job>.csv); il riepilogo va in $LOG.
#
# $LOG non deve esistere (i log non si sovrascrivono). Uscite in
# $WMB/artifacts/_runs/diagnostics/inventory/: ingressi condivisi (inputs/, pool/), tabelle,
# riepiloghi e selezione separati per commit del submodule (<c> = primi 12 caratteri: tables/<c>/,
# summary_<c>.*, pool_tables/<c>/, pool_summary_<c>.*, mcss_selftest_<c>.*), così il cambio di
# libreria non si scontra con le uscite di P0; log del job in $WMB/logs/inventory_sites_<job>.log.
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
if [ "$MODE" = candidates ] && [ ! -f "$LIB/multimark/cli/candidates.py" ]; then
    echo "multimark.cli.candidates not found in build/patched/multimark (submodule at ${LINK:0:12})." >&2
    echo "Update third_party/multimark to a commit that contains it, then rerun" >&2
    echo "bash scripts/apply_patches.sh multimark." >&2
    exit 1
fi

variant() {  # $1 = variante -> "FLAGS|NOME"
    case "$1" in
        base)     echo "--channels extended|canonical-kind_guards-on_params-off" ;;
        base_mod) echo "--channels extended --module-level on|canonical-kind_guards-on_params-off_modlevel-on" ;;
        mcss)     echo "--channels mcss8 --canonical paper --include-params|canonical-paper_guards-on_params-on" ;;
        mcss_mod) echo "--channels mcss8 --canonical paper --include-params --module-level on|canonical-paper_guards-on_params-on_modlevel-on" ;;
        params)   echo "--channels extended --include-params|canonical-kind_guards-on_params-on" ;;
        noguards) echo "--channels extended --no-guards|canonical-kind_guards-off_params-off" ;;
        *) return 1 ;;
    esac
}

if [ "$MODE" != inventory ] && [ "$#" -gt 0 ]; then
    echo "$MODE takes no arguments" >&2
    exit 2
fi
if [ "$MODE" = selftest ]; then
    VARIANTS=(base mcss_mod)
elif [ "$MODE" = candidates ]; then
    VARIANTS=()
elif [ "$#" -gt 0 ]; then
    VARIANTS=("$@")
else
    VARIANTS=(base base_mod mcss mcss_mod)
fi
FLAGS=()
NAMES=()
for v in ${VARIANTS[@]+"${VARIANTS[@]}"}; do
    if ! spec="$(variant "$v")"; then
        echo "unknown variant '$v' (base, base_mod, mcss, mcss_mod, params, noguards)" >&2
        exit 2
    fi
    FLAGS+=("${spec%%|*}")
    NAMES+=("${spec#*|}")
done

OUT="$WMB/artifacts/_runs/diagnostics/inventory"
C="${LINK:0:12}"  # uscite per commit della libreria
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
    TABLES="$OUT/pool_tables/$C"
    SUMMARY="$OUT/pool_summary_$C"
    SELECTION="$OUT/mcss_selftest_$C"
else
    EXPORT="$BENCH_PY scripts/export_inventory_inputs.py --out-dir $OUT/inputs"
    INPUT="$OUT/inputs"
    TABLES="$OUT/tables/$C"
    SUMMARY="$OUT/summary_$C"
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
    if [ "$MODE" = candidates ]; then
        # Un --input per gruppo esportato (etichetta = nome del gruppo); stima sui gruppi di L1 dev.
        cat <<SBATCH
export PYTHONPATH=$LIB
$POSTHOC_PY -c "import multimark, sys; f = multimark.__file__; sys.exit(0 if f.startswith('$LIB/') else 'multimark imported from ' + f)"
INPUTS=()
EST=()
for f in $INPUT/*.jsonl; do
    g=\$(basename "\$f" .jsonl)
    INPUTS+=(--input "\$g=\$f")
    case "\$g" in L1_dev_*) EST+=("\$g") ;; esac
done
if [ "\${#EST[@]}" -lt 3 ]; then echo "expected 3 L1 dev groups in $INPUT, found: \${EST[*]:-none}" >&2; exit 1; fi
CAND=$OUT/candidates
mkdir -p "\$CAND"
$POSTHOC_PY -m multimark.cli.candidates "\${INPUTS[@]}" --estimate-groups "\$(IFS=,; echo "\${EST[*]}")" --id-field sample_id --output "\$CAND/samples_\$SLURM_JOB_ID.csv" --summary "\$CAND/summary_\$SLURM_JOB_ID.txt" --summary-json "\$CAND/summary_\$SLURM_JOB_ID.json"
{
  echo "# candidates of multimark (scripts/inventory_sites.sh candidates; python -m multimark.cli.candidates)"
  echo "# date \$(date -Iseconds), host \$(hostname), wm-bench \$(git rev-parse HEAD), multimark $LINK"
  echo "# inputs: $INPUT (<gruppo>.jsonl.manifest.json): \${INPUTS[*]}"
  echo "# estimate groups: \${EST[*]}"
  echo "# outputs: \$CAND/{summary,samples}_\$SLURM_JOB_ID.*"
  echo
  cat "\$CAND/summary_\$SLURM_JOB_ID.txt"
} > $LOG
echo "[OK] $LOG"
SBATCH
    else
        for i in "${!VARIANTS[@]}"; do
            echo "$POSTHOC_PY scripts/inventory_sites.py --input $INPUT --out-dir $TABLES ${FLAGS[$i]} --summary $SUMMARY.${NAMES[$i]}.txt --summary-json $SUMMARY.${NAMES[$i]}.json"
        done
        if [ "$MODE" = selftest ]; then
            echo "$POSTHOC_PY scripts/select_mcss_selftest.py --pool $POOL --mcss-table $TABLES/L2_codenet_pool.canonical-paper_guards-on_params-on_modlevel-on.csv --base-table $TABLES/L2_codenet_pool.canonical-kind_guards-on_params-off.csv --out $SELECTION.jsonl --report ${SELECTION}_report.txt"
        fi
        echo "{"
        echo "  echo \"# $MODE of multimark sites (scripts/inventory_sites.sh $([ "$MODE" = selftest ] && echo selftest || echo "${VARIANTS[*]}"))\""
        echo "  echo \"# date \$(date -Iseconds), host \$(hostname), wm-bench \$(git rev-parse HEAD), multimark $LINK\""
        echo "  echo \"# tables: $TABLES\""
        echo "  echo \"# inputs: $INPUT (<gruppo>.jsonl.manifest.json)\""
        if [ "$MODE" = selftest ]; then
            echo "  echo \"# selection: $SELECTION.jsonl\"; echo"
            echo "  cat ${SELECTION}_report.txt"
        fi
        for i in "${!VARIANTS[@]}"; do
            echo "  echo; echo \"######## variant ${VARIANTS[$i]} (${NAMES[$i]})\"; echo"
            echo "  cat $SUMMARY.${NAMES[$i]}.txt"
        done
        echo "} > $LOG"
        echo "echo \"[OK] $LOG\""
    fi
} > "$JOB"

ID=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
    -c 4 --mem 16G -t 60 ${EXCLUDE[@]+"${EXCLUDE[@]}"} ${DEP[@]+"${DEP[@]}"} \
    --job-name wmb-inventory-sites \
    -o "$WMB/logs/inventory_sites_%j.log" "$JOB")
echo "job $ID (defq): log $WMB/logs/inventory_sites_$ID.log -> $LOG"
