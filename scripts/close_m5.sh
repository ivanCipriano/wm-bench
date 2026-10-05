#!/usr/bin/env bash
# Verifica finale della Milestone 5 sul cluster (output anche in docs/milestone_logs/M5_cluster.txt).
#
#   bash scripts/close_m5.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export WMB=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench
export PATH="$WMB/bench-core/bin:$PATH"
LOG="$REPO_ROOT/docs/milestone_logs/M5_cluster.txt"
ART="$WMB/artifacts"

{
echo "##### chiusura della Milestone 5 ($(date '+%Y-%m-%d %H:%M'), $USER)"
git log --oneline -1
git status --short

echo "##### job watermark degli ultimi giorni (entrambi gli utenti)"
sacct -a -A did_tesi_nlp_330 -S now-3days -X \
    --format=JobID%14,User%22,JobName%34,State%14,ExitCode,Elapsed,NodeList%12 | grep -E "JobID|wmb-watermark|wmb-oracle"
echo "##### job ancora in coda (atteso: nessuno della fase watermark)"
squeue -A did_tesi_nlp_330 -o "%.18i %.12u %.34j %.8T %.10M"

echo "##### artefatti (attesi: 8 parquet = 2 modelli x 4 linguaggi, L1 dev)"
ls "$ART"/watermarked/stone/*/*/L1_*_dev.parquet | wc -l
ls "$ART"/watermarked/stone/*/*/

echo "##### ripresa dei worker (righe 'already done' > 0 dopo un'interruzione)"
grep -h "items, .* already done" "$ART"/_runs/watermark/stone/*/*/*/worker.log | sort | uniq -c
grep -h "timeout after\|attempt 1 ended" "$ART"/_runs/watermark/stone/*/*/*/worker.log | head

echo "##### criteri di accettazione M5"
WMB_REQUIRE_DATA=1 python -m pytest -q -rs -m data tests/integration/test_l1_watermark.py
WMB_REQUIRE_DATA=1 python -m pytest -q -rs tests/unit/test_worker_client.py

echo "##### riepilogo dei manifest"
python - <<'PYEOF'
import json, pathlib
root = pathlib.Path("/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts/watermarked/stone")
for m in sorted(root.glob("*/*/*.manifest.json")):
    d = json.loads(m.read_text()); e = d["extra"]
    print(f"{m.parent.parent.name:20s} {m.name.split('.')[0]:22s} rows={d['n_rows_out']}/{d['n_rows_expected']} "
          f"status={e['embed_status_counts']} extraction={e['extraction_rate']} attempts={e['worker']['attempts']} "
          f"missing={e['worker']['missing_results']} cfg={d['config_hash']}")
PYEOF

echo "##### completezza (atteso: total: 0 ran, 8 skipped)"
bench stage=watermark "methods=[stone]" "levels=[L1]" "splits=[dev]" 2>&1 | tail -n 1
} 2>&1 | tee -a "$LOG"

echo
echo "Fatto. Ora: git add docs/milestone_logs/M5_cluster.txt tests/fixtures/oracle/stone && git commit -m '[M5] logs: chiusura STONE' && git push"
