#!/usr/bin/env bash
# Turno 1 della Milestone 9, tutto in un solo job CPU su defq (non serve tenere aperto il terminale):
#
#   sbatch scripts/m9_codenet_select.sh
#
# Passi, in ordine (ognuno si salta se già fatto, quindi se il job finisce per tempo basta rilanciarlo):
#   1. candidati CodeNet (criteri su metadati e testo);
#   2. estrazione selettiva delle sottomissioni dall'archivio;
#   3. validazione nella sandbox (riprende da dove era arrivata);
#   4. scelta finale (configs/dataset/codenet_selected.yaml) oppure STOP se meno di 250 idonei;
#   5. anteprima dei prompt e confronto dei negativi di sviluppo.
# Log: docs/milestone_logs/M9_codenet_select.txt, M9_codenet_preview.txt, M9_negatives_compare.txt;
# log del job: $WMB/tmp/codenet/job_<id>.out
#SBATCH --partition=defq
#SBATCH --account=did_tesi_nlp_330
#SBATCH --qos=did_tesi_nlp_330_defq_qos
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=08:30:00
#SBATCH --job-name=wmb-m9-codenet
#SBATCH --output=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/tmp/codenet/job_%j.out
set -euo pipefail
umask 002

WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
REPO=/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench
WORK="$WMB/tmp/codenet"
LOGS="$REPO/docs/milestone_logs"
export PATH="$WMB/bench-core/bin:$PATH"
module load apptainer/apptainer.module
cd "$REPO"
mkdir -p "$WORK"
echo "##### node $(hostname), $(date)"

if [ ! -f "$WORK/candidates.json" ]; then
    echo "##### 1. candidates"
    python scripts/codenet_select.py candidates | tee "$LOGS/M9_codenet_select.txt"
fi

if [ ! -f "$WORK/extracted.ok" ]; then
    echo "##### 2. extraction"
    first=$(tar -tzf "$WMB/datasets/Project_CodeNet.tar.gz" | head -1 || true)
    case "$first" in
        Project_CodeNet/*) ;;
        *) echo "unexpected archive prefix: $first" >&2; exit 1 ;;
    esac
    (cd "$WMB/datasets" && tar -xzf Project_CodeNet.tar.gz -T "$WORK/extract_list.txt")
    wanted=$(wc -l < "$WORK/extract_list.txt")
    found=$(sed 's#^#'"$WMB"'/datasets/#' "$WORK/extract_list.txt" | xargs -d '\n' ls 2>/dev/null | wc -l)
    echo "extracted $found of $wanted files" | tee -a "$LOGS/M9_codenet_select.txt"
    [ "$found" -eq "$wanted" ] || { echo "missing files after extraction" >&2; exit 1; }
    touch "$WORK/extracted.ok"
fi

echo "##### 3. validate"
python scripts/codenet_select.py validate | tee -a "$LOGS/M9_codenet_select.txt"

echo "##### 4. finalize"
if ! python scripts/codenet_select.py finalize | tee -a "$LOGS/M9_codenet_select.txt"; then
    echo "##### STOP: fewer than 250 eligible problems; preview and comparison skipped"
    exit 0
fi
[ -f configs/dataset/codenet_selected.yaml ] || { echo "STOP (see the log)"; exit 0; }

echo "##### 5. preview and dev negatives comparison"
python scripts/codenet_preview.py > "$LOGS/M9_codenet_preview.txt"
python scripts/compare_dev_negatives.py > "$LOGS/M9_negatives_compare.txt"
echo "##### done $(date)"
