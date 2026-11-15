#!/usr/bin/env bash
# Sottomissioni CodeNet Python in più dei problemi di sviluppo (PIPELINE v1.2 §4.4 punto 2,
# decisione K dell'utente del 15 novembre 2026): job CPU su defq, dal nodo di login
#
#   LOG=docs/milestone_logs/P1_codenet_extra_dev.txt sbatch scripts/codenet_extra_dev.sh
#
# Passi (ognuno si salta se già fatto: se il job finisce per tempo basta rilanciarlo con un LOG
# nuovo):
#   1. lista: scripts/codenet_extra_dev.py (bench-core; pick_submissions con limit 60, solo
#      Python, solo split == dev, senza le sottomissioni già estratte in M9) ->
#      $WMB/tmp/codenet/extra_dev_list.{txt,json};
#   2. estrazione con tar -xzf ... -T nella cartella separata $WMB/datasets/codenet_extra_dev
#      (i file finiscono in Project_CodeNet/data/<pid>/Python/ sotto di essa, non nella
#      cartella di M9), poi controllo che ci siano tutti.
# Il confronto per impronta del codice e il pool allargato (ruolo extra_dev) si fanno dopo,
# nell'export di `inventory_sites.sh selftest_extra`.
# Log: $LOG (non deve esistere); log del job: $WMB/logs/codenet_extra_dev_<id>.log
#SBATCH --partition=defq
#SBATCH --account=did_tesi_nlp_330
#SBATCH --qos=did_tesi_nlp_330_defq_qos
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --job-name=wmb-codenet-extra-dev
#SBATCH --output=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/logs/codenet_extra_dev_%j.log
set -euo pipefail
set -o noclobber
umask 002

WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
REPO=/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench
WORK="$WMB/tmp/codenet"
EXTRA="$WMB/datasets/codenet_extra_dev"
ARCHIVE="$WMB/datasets/Project_CodeNet.tar.gz"
PY="$WMB/bench-core/bin/python"
cd "$REPO"
if [ -z "${LOG:-}" ]; then
    echo "LOG is required, e.g. LOG=docs/milestone_logs/P1_codenet_extra_dev.txt" >&2
    exit 2
fi
case "$LOG" in /*) ;; *) LOG="$REPO/$LOG" ;; esac
if [ -e "$LOG" ]; then
    echo "$LOG already exists: choose a new name (logs are never overwritten)" >&2
    exit 1
fi
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 PYTHONHASHSEED=0
mkdir -p "$WORK" "$EXTRA"
{
    echo "# CodeNet extra submissions of the development problems (scripts/codenet_extra_dev.sh)"
    echo "# date $(date -Iseconds), host $(hostname), wm-bench $(git rev-parse HEAD)"
    echo "# list: $WORK/extra_dev_list.{txt,json}; extraction: $EXTRA"
    echo
} > "$LOG"

echo "##### 1. list"
"$PY" scripts/codenet_extra_dev.py --limit 60 --work "$WORK" | tee -a "$LOG"

LIST="$WORK/extra_dev_list.txt"
if [ ! -f "$EXTRA/extracted.ok" ]; then
    echo "##### 2. extraction"
    first=$(tar -tzf "$ARCHIVE" | head -1 || true)
    case "$first" in
        Project_CodeNet/*) ;;
        *) echo "unexpected archive prefix: $first" >&2; exit 1 ;;
    esac
    status=0
    (cd "$EXTRA" && tar -xzf "$ARCHIVE" -T "$LIST") || status=$?
    echo "tar exit status: $status" | tee -a "$LOG"
fi
wanted=$(wc -l < "$LIST")
found=0
missing=()
while IFS= read -r rel; do
    if [ -f "$EXTRA/$rel" ]; then
        found=$((found + 1))
    else
        missing+=("$rel")
    fi
done < "$LIST"
echo "extracted $found of $wanted files into $EXTRA" | tee -a "$LOG"
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'missing: %s\n' "${missing[@]:0:20}" | tee -a "$LOG"
    exit 1
fi
[ -f "$EXTRA/extracted.ok" ] || touch "$EXTRA/extracted.ok"
problems=$(find "$EXTRA/Project_CodeNet/data" -mindepth 1 -maxdepth 1 -type d | wc -l)
echo "problems with extracted files: $problems" | tee -a "$LOG"
echo "[OK] $LOG"
