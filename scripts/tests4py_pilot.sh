#!/usr/bin/env bash
# Pilota di Tests4Py (L4; scripts/tests4py_pilot.py, criteri in cluster_info §8.7). Sul nodo di
# login (serve la rete), in una finestra tmux perché può durare ore:
#
#   bash scripts/tests4py_pilot.sh            # ambiente + pilota (riprendibile)
#   bash scripts/tests4py_pilot.sh report     # verdetto; con la quota: QUOTA_GB=<n> ...
#
# Ambiente separato da bench-core: $WMB/tests4py/venv con tests4py==1.0.0. HOME=$WMB/tests4py/home,
# così pyenv, le cache e gli ambienti dei progetti non finiscono nella home (quota) e lo spazio si
# misura per intero. Report:
#   bash scripts/tests4py_pilot.sh report | tee docs/milestone_logs/M13_tests4py_pilot.txt
# (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export WMB
BASE="$WMB/tests4py"
mkdir -p "$BASE/home"
export HOME="$BASE/home"
if [ ! -x "$BASE/venv/bin/t4p" ]; then
    START=$(date +%s)
    python3 -m venv "$BASE/venv"
    "$BASE/venv/bin/pip" install --quiet tests4py==1.0.0
    echo "venv: $(( $(date +%s) - START )) s" >> "$BASE/setup_times.txt"
fi
export PATH="$BASE/venv/bin:$PATH"
cd "$REPO_ROOT"
if [ "${1:-run}" = report ]; then
    python3 scripts/tests4py_pilot.py report ${QUOTA_GB:+--quota-gb "$QUOTA_GB"}
else
    python3 scripts/tests4py_pilot.py run
fi
