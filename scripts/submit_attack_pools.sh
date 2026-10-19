#!/usr/bin/env bash
# Raccolte di commenti e nomi per T1.3(b) e T1.4 (fase attack_pools, ADR-012): un job CPU per
# linguaggio su defq. Si lancia dal nodo di login:
#
#   bash scripts/submit_attack_pools.sh            # 4 job (python, java, cpp, javascript)
#   bash scripts/submit_attack_pools.sh --dry-run
#
# Poi: python scripts/thestack_partition_report.py > docs/milestone_logs/M10_attack_pools.txt
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
umask 002
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
ARGS=(--jobs 4 "stage=attack_pools" "languages=[python,java,cpp,javascript]")
bench submit --dry-run "${ARGS[@]}" | grep -v "parameters:"
if [ "${1:-}" = "--dry-run" ]; then
    exit 0
fi
bench submit "${ARGS[@]}" | grep -v "parameters:"
squeue -u "$USER" -o "%.18i %.50j %.8T %.10M"
