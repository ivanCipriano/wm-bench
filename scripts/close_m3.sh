#!/usr/bin/env bash
# Verifica finale della Milestone 3 sul cluster; tutto l'output va anche in
# docs/milestone_logs/M3_cluster.txt. Da lanciare dal nodo di login quando la baseline è finita.
#
# Uso:  bash scripts/close_m3.sh
set -uo pipefail   # niente -e: ogni controllo va eseguito e registrato anche se uno fallisce

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export WMB=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench
export PATH="$WMB/bench-core/bin:$PATH"
LOG="$REPO_ROOT/docs/milestone_logs/M3_cluster.txt"
ART="$WMB/artifacts"

{
echo "##### 7. chiusura della Milestone 3 ($(date '+%Y-%m-%d %H:%M'), $USER)"
git log --oneline -1
git status --short

echo "##### job della baseline di oggi (entrambi gli utenti)"
sacct -a -A did_tesi_nlp_330 -S today -X \
    --format=JobID%14,User%22,JobName%34,State%14,ExitCode,Elapsed,NodeList%12 | grep -E "JobID|wmb"
echo "##### job ancora in coda (atteso: nessuno della baseline)"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.34j %.8T %.10M"

echo "##### artefatti (attesi: 16 parquet, nessun file parziale)"
ls "$ART"/baseline/*/L1_*.parquet | wc -l
ls -la "$ART"/baseline/*/_partial/ 2>/dev/null

echo "##### criteri di accettazione M3 (e controlli M2 sugli stessi dati)"
WMB_REQUIRE_DATA=1 python -m pytest -q -rs -m data \
    tests/integration/test_l1_baseline.py tests/integration/test_l1_data.py tests/integration/test_loc_oracle.py

echo "##### riepilogo della baseline"
python scripts/report_baseline.py --artifacts "$ART"

echo "##### completezza (atteso: total: 0 ran, 16 skipped; nessun modello caricato)"
bench stage=generate_baseline "levels=[L1]" "splits=[dev,test]" 2>&1 | tail -n 1

echo "##### doctor"
bench doctor | tail -n 3
} 2>&1 | tee -a "$LOG"

echo
echo "Fatto. Ora: git add docs/milestone_logs/M3_cluster.txt && git commit -m '[M3] logs: chiusura baseline L1' && git push"
