"""La rilevazione di ACW in parallelo dà gli stessi risultati di quella in sequenza?

Uso (con bench-core e ``source ~/.bashrc`` per SOURCERY_TOKEN; vedi check_acw_parallel.sh):
    python scripts/check_acw_parallel.py RUN_DIR [--n 100]

``RUN_DIR`` è la cartella di una rilevazione di ACW già fatta con lo shim in sequenza
(``$WMB/artifacts/_runs/detect/acw/...``, con ``request.json``, ``items.jsonl`` e
``results.jsonl``). Lo script riesegue con lo shim attuale (regole in parallelo, ``RULE_WORKERS``)
i primi ``n`` item già scritti, in una cartella temporanea, e confronta item per item punteggio,
esito di ogni regola (``rule_unchanged``) e applicazione congiunta (``joint_unchanged``). Stampa
anche il tempo medio per lotto prima e dopo. Non scrive nulla negli artefatti.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from bench_contracts import WorkerItem, WorkerRequest
from bench_contracts.jsonl import iter_jsonl, write_jsonl

from bench.config.builder import load_experiment
from bench.pipeline.stages.watermark import make_adapter

MAX_SHOWN = 10  # item diversi stampati per esteso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--n", type=int, default=100)
    args = parser.parse_args()
    run_dir: Path = args.run_dir
    serial = {r["item_id"]: r for r in iter_jsonl(run_dir / "results.jsonl")}
    items = [WorkerItem.from_dict(r) for r in iter_jsonl(run_dir / "items.jsonl")]
    items = [it for it in items if it.item_id in serial][: args.n]
    if not items:
        sys.stdout.write(f"no completed items in {run_dir}\n")
        return 1
    cfg = load_experiment(["paths=cluster", "stage=detect"])
    adapter = make_adapter(cfg, "acw")
    work = cfg.paths.tmp / "check_acw_parallel" / f"{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    request.update(
        items_path=str(work / "items.jsonl"),
        output_path=str(work / "results.jsonl"),
        log_path=str(work / "worker.log"),
    )
    write_jsonl(work / "items.jsonl", [it.to_dict() for it in items])
    start = time.perf_counter()
    run = adapter.run_worker(WorkerRequest.from_dict(request), items)
    elapsed = time.perf_counter() - start
    batch = int(serial[items[0].item_id]["extra"].get("batch_size") or 50)
    n_batches = -(-len(items) // batch)
    serial_s = sum(float(serial[it.item_id].get("elapsed_s") or 0.0) for it in items)
    sys.stdout.write(
        f"##### {len(items)} item da {run_dir}\n"
        f"tempo per lotto: sequenza {serial_s / n_batches:.0f} s, parallelo "
        f"{elapsed / n_batches:.0f} s (compresi avvio, login e canarino)\n"
    )
    diff = 0
    for result in run.results:
        old = serial[result.item_id]
        same = (
            result.status == old["status"]
            and result.score == old["score"]
            and result.extra.get("rule_unchanged") == old["extra"].get("rule_unchanged")
            and result.extra.get("joint_unchanged") == old["extra"].get("joint_unchanged")
        )
        if not same:
            diff += 1
            if diff <= MAX_SHOWN:
                rules = {
                    k: (v, result.extra.get("rule_unchanged", {}).get(k))
                    for k, v in old["extra"].get("rule_unchanged", {}).items()
                    if result.extra.get("rule_unchanged", {}).get(k) != v
                }
                sys.stdout.write(
                    f"  DIVERSO {result.item_id}: score {old['score']} -> {result.score}, "
                    f"joint {old['extra'].get('joint_unchanged')} -> "
                    f"{result.extra.get('joint_unchanged')}, regole (prima, dopo) {rules}\n"
                )
    verdict = "IDENTICI" if diff == 0 else f"DIVERSI in {diff} item"
    sys.stdout.write(f"risultati: {verdict} su {len(run.results)}\n")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
