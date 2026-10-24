#!/usr/bin/env bash
# Pilota di T2.2(b) su L1 dev C++ (scripts/pilot_t2_2_cpp.py), prima del lancio completo. Dal nodo
# di login:
#
#   bash scripts/pilot_t2_2_cpp.sh
#
# Due job: riscrittura su gpuq (Llama-3.1-8B-Instruct, fino a 200 campioni), poi test su defq
# (originale, attaccato, riparato; parte solo se il primo è riuscito). A job finiti:
#   python scripts/pilot_t2_2_cpp.py report > docs/milestone_logs/M11_t2_2_cpp_pilot.txt
# Con "bash scripts/pilot_t2_2_cpp.sh execute" rilancia solo i test (riscrittura già fatta).
# Scrive solo in $WMB/tmp/t2_2_cpp_pilot/; log in $WMB/logs/pilot_t2_2_cpp_<job>.log.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
job_script() {  # $1 = modalità, $2 = righe in più prima del comando
    local path="$WMB/logs/pilot_t2_2_cpp_$1.sbatch"
    cat > "$path" <<SBATCH
#!/bin/bash -l
set -e
umask 002
export PATH=$WMB/bench-core/bin:\$PATH
export HF_HOME=$WMB/hf_cache HF_HUB_CACHE=$WMB/hf_cache/hub
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONHASHSEED=0
$2
cd $REPO_ROOT
echo "##### pilot_t2_2_cpp $1: nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
python scripts/pilot_t2_2_cpp.py $1
SBATCH
    echo "$path"
}
submit_execute() {  # $@ = opzioni sbatch in più (dipendenza)
    sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos \
        -c 16 --mem 32G -t 120 ${EXCLUDE[@]+"${EXCLUDE[@]}"} "$@" \
        --job-name wmb-pilot-t2_2-cpp-exec -o "$WMB/logs/pilot_t2_2_cpp_%j.log" \
        "$(job_script execute "module load apptainer/apptainer.module")"
}
if [ "${1:-}" = "execute" ]; then  # solo i test, su generate.json già scritto
    EXE=$(submit_execute)
    echo "job: execute $EXE (defq); log: $WMB/logs/pilot_t2_2_cpp_$EXE.log"
    exit 0
fi
GEN=$(sbatch --parsable -p gpuq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_gpuq_qos --gres=gpu:1 \
    -c 8 --mem 64G -t 120 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-pilot-t2_2-cpp \
    -o "$WMB/logs/pilot_t2_2_cpp_%j.log" "$(job_script generate "")")
EXE=$(submit_execute --dependency "afterok:$GEN")
echo "jobs: generate $GEN (gpuq), execute $EXE (defq, after $GEN)"
echo "logs: $WMB/logs/pilot_t2_2_cpp_$GEN.log, $WMB/logs/pilot_t2_2_cpp_$EXE.log"
