#!/usr/bin/env bash
# Verifica finale della Milestone 4 sul cluster; tutto l'output va anche in
# docs/milestone_logs/M4_cluster.txt. Da lanciare dal nodo di login a esecuzione finita.
#
# Uso:  bash scripts/close_m4.sh
set -uo pipefail   # niente -e: ogni controllo va eseguito e registrato anche se uno fallisce

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export WMB=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench
export PATH="$WMB/bench-core/bin:$PATH"
LOG="$REPO_ROOT/docs/milestone_logs/M4_cluster.txt"
ART="$WMB/artifacts"

{
echo "##### chiusura della Milestone 4 ($(date '+%Y-%m-%d %H:%M'), $USER)"
git log --oneline -1
git status --short

echo "##### job dell'esecuzione di oggi (entrambi gli utenti)"
sacct -a -A did_tesi_nlp_330 -S today -X \
    --format=JobID%14,User%22,JobName%34,State%14,ExitCode,Elapsed,NodeList%12 | grep -E "JobID|wmb"
echo "##### job ancora in coda (atteso: nessuno dell'esecuzione)"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.34j %.8T %.10M"

echo "##### artefatti (attesi: ground truth, 4 canoniche, 16 baseline; nessun file parziale)"
ls "$ART"/execution/_groundtruth/*.parquet
ls "$ART"/execution/canonical/L1_*.parquet | wc -l
ls "$ART"/execution/llm_baseline/*/L1_*.parquet | wc -l
ls -la "$ART"/execution/*/_partial "$ART"/execution/*/*/_partial 2>/dev/null

echo "##### manifest: tempi, worker, versioni dell'immagine"
python - <<'PYEOF'
import json, pathlib
root = pathlib.Path("/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts/execution")
for m in sorted(root.glob("**/*.parquet.manifest.json")):
    d = json.loads(m.read_text())
    e = d["extra"]
    print(f"{str(m.relative_to(root)).removesuffix('.parquet.manifest.json'):45s} "
          f"rows={d['n_rows_out']} s/problem={e.get('seconds_per_problem', {}).get('mean')} "
          f"workers={e.get('workers')} node={d['provenance']['node']} dirty={d['provenance']['repo_dirty']}")
first = json.loads(next(root.glob("canonical/*.manifest.json")).read_text())
print("sandbox image:", first["sandbox_image_hash"])
print(json.dumps(first["extra"]["sandbox_versions"], indent=1, sort_keys=True))
PYEOF

echo "##### criteri di accettazione M4 (e M3 sugli stessi dati)"
WMB_REQUIRE_DATA=1 python -m pytest -q -rs -m data \
    tests/integration/test_l1_execution.py tests/integration/test_l1_baseline.py

echo "##### riepilogo dell'esecuzione"
python scripts/report_execution.py --artifacts "$ART"

echo "##### completezza (atteso: total: 0 ran, 20 skipped)"
bench stage=execute "levels=[L1]" "splits=[dev,test]" 2>&1 | tail -n 1

echo "##### doctor"
module load apptainer/apptainer.module 2>/dev/null
bench doctor > /tmp/wmb_doctor_$$.txt; grep -F -A5 '[apptainer]' /tmp/wmb_doctor_$$.txt; tail -n 1 /tmp/wmb_doctor_$$.txt; rm -f /tmp/wmb_doctor_$$.txt
} 2>&1 | tee -a "$LOG"

echo
echo "Fatto. Ora: git add docs/milestone_logs/M4_cluster.txt && git commit -m '[M4] logs: chiusura esecuzione L1' && git push"
