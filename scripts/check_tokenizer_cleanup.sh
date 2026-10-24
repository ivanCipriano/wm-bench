#!/usr/bin/env bash
# Pulizia degli spazi in decodifica nei 6 ambienti (scripts/check_tokenizer_cleanup.py) per i due
# generatori e per l'attaccante. Dal nodo di login:
#
#   bash scripts/check_tokenizer_cleanup.sh
#
# Un job breve su defq (solo tokenizer, niente GPU). A job finito il log contiene una riga JSON per
# (ambiente, modello): $WMB/logs/check_tokenizer_cleanup_<job>.log.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
HUB="$WMB/hf_cache/hub"
mkdir -p "$WMB/logs"
EXCLUDE=()
if [ -n "${WMB_EXCLUDE_NODES:-}" ]; then
    EXCLUDE=(--exclude "$WMB_EXCLUDE_NODES")
fi
SCRIPT="$WMB/logs/check_tokenizer_cleanup.sbatch"
cat > "$SCRIPT" <<SBATCH
#!/bin/bash -l
umask 002
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
cd $REPO_ROOT
echo "##### check_tokenizer_cleanup: nodo \$(hostname), commit \$(git rev-parse --short HEAD)"
MODELS=(
  "qwen25_coder_7b $HUB/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242"
  "deepseek_coder_6p7b $HUB/models--deepseek-ai--deepseek-coder-6.7b-instruct/snapshots/e5d64addd26a6a1db0f9b863abf6ee3141936807"
  "llama31_8b_attacker $HUB/models--meta-llama--Llama-3.1-8B-Instruct/snapshots/0e9e39f249a16976918f6564b8830bc894c89659"
)
for ENV in bench-core sweet stone promptmark mcgmark acw; do
  if [ "\$ENV" = bench-core ]; then PY=$WMB/bench-core/bin/python; else PY=$WMB/miniforge3/envs/\$ENV/bin/python; fi
  for M in "\${MODELS[@]}"; do
    set -- \$M
    \$PY scripts/check_tokenizer_cleanup.py "\$ENV" "\$1" "\$2" 2>&1 | grep -v "^Special tokens\|warn" || echo "{\"env\": \"\$ENV\", \"model\": \"\$1\", \"error\": true}"
  done
done
echo "##### done"
SBATCH
JOB=$(sbatch --parsable -p defq -A did_tesi_nlp_330 --qos did_tesi_nlp_330_defq_qos -c 2 --mem 8G \
    -t 20 ${EXCLUDE[@]+"${EXCLUDE[@]}"} --job-name wmb-check-tokenizer-cleanup \
    -o "$WMB/logs/check_tokenizer_cleanup_%j.log" "$SCRIPT")
echo "job: $JOB (defq); a job finito: cat $WMB/logs/check_tokenizer_cleanup_$JOB.log"
