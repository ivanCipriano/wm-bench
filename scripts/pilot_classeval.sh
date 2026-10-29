#!/usr/bin/env bash
# Pilota di ClassEval (scripts/pilot_classeval.py), prima della generazione di L3. Dal nodo di
# login, dopo aver costruito l'immagine di L3 (bash scripts/build_sandbox.sh l3):
#
#   bash scripts/pilot_classeval.sh
#
# Due job: generazione su gpuq (10 classi, Qwen e DeepSeek, 6 campioni ciascuno), poi test su defq
# con l'immagine sandbox_l3 (parte solo se il primo è riuscito). A job finiti:
#   python scripts/pilot_classeval.py report | tee docs/milestone_logs/M13_classeval_pilot.txt
# (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)
# Con "bash scripts/pilot_classeval.sh execute" rilancia solo i test (generazione già fatta).
# Scrive solo in $WMB/tmp/classeval_pilot/; log in $WMB/logs/pilot_classeval_<job>.log.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
mkdir -p "$WMB/logs"
if [ ! -f "$WMB/containers/sandbox_l3.sif.sha256" ]; then
    echo "immagine di L3 assente: prima bash scripts/build_sandbox.sh l3" >&2
    exit 1
fi
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
job_script() {  # $1 = modalità, $2 = righe in più prima del comando
    local path="$WMB/logs/pilot_classeval_$1.sbatch"
    cat > "$path" <<SBATCH
#!/bin/bash -l
set -e
umask 002
export PATH=$WMB/bench-core/bin:\$PATH
export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
$2
cd $REPO_ROOT
echo "##### pilot_classeval $1: nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
python scripts/pilot_classeval.py $1
SBATCH
    echo "$path"
}
submit_execute() {  # $@ = opzioni sbatch in più (dipendenza)
    sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
        -c 8 --mem 32G -t 60 ${EXCLUDE[@]+"${EXCLUDE[@]}"} "$@" \
        --job-name wmb-pilot-classeval-exec -o "$WMB/logs/pilot_classeval_%j.log" \
        "$(job_script execute "module load apptainer/apptainer.module")"
}
if [ "${1:-}" = "execute" ]; then  # solo i test, su generate.json già scritto
    EXE=$(submit_execute)
    echo "job: execute $EXE (defq); log: $WMB/logs/pilot_classeval_$EXE.log"
    exit 0
fi
GEN=$(sbatch --parsable -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos --gres=gpu:1 \
    -c 8 --mem 64G -t 180 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-pilot-classeval \
    -o "$WMB/logs/pilot_classeval_%j.log" "$(job_script generate "")")
EXE=$(submit_execute --dependency "afterok:$GEN")
echo "jobs: generate $GEN (gpuq), execute $EXE (defq, after $GEN)"
echo "logs: $WMB/logs/pilot_classeval_$GEN.log, $WMB/logs/pilot_classeval_$EXE.log"
