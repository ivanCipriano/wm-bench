#!/usr/bin/env bash
# Smoke test della chiusura (fase 2, docs/milestone_logs/M13_M14_plan.md): ogni catena nuova su un
# problema per combinazione di metodo, modello e livello, in $WMB/smoke/ (gli artefatti veri non si
# toccano: WMB_WRITE_ROOT, e lo store si ferma con un errore se prova a scrivere fuori).
#
#   tmux new -s smoke
#   bash scripts/smoke_test.sh               # prepara la cartella (una volta) e avanza le catene
#                                            # ogni 2 minuti finché sono tutte finite o ferme
# Esito (da un'altra finestra, in qualsiasi momento):
#   python scripts/chains.py status --smoke --brief            # una riga per catena
#   python scripts/chains.py status --smoke --logs             # passi non finiti e log dei falliti
#
# Un solo account per tutte le catene (--owner all). I passi di ACW su L2 test e L3 restano HELD
# finché non c'è --with-acw (licenza di Sourcery da confermare): per includerli
#   WITH_ACW=1 bash scripts/smoke_test.sh
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
cd "$REPO_ROOT"
umask 002
mkdir -p "$WMB/smoke"
if [ ! -d "$WMB/smoke/artifacts" ]; then
    python scripts/smoke_seed.py | tee "$WMB/smoke/seed_$(date +%Y%m%d_%H%M%S).log" \
        || { echo "smoke seed failed" >&2; exit 1; }
fi
ACW=()
[ "${WITH_ACW:-0}" = 1 ] && ACW=(--with-acw)
python scripts/chains.py loop --smoke --owner all --every 120 ${ACW[@]+"${ACW[@]}"}
