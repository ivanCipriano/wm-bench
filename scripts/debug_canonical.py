"""Diagnosi delle soluzioni canoniche di HumanEval+/MBPP+ che non passano (Milestone 4).

Uso (su un nodo defq, con Apptainer caricato; vedi scripts/debug_canonical.sh):
    python scripts/debug_canonical.py humaneval/32 humaneval/139 mbpp/255 [--load 16] [--repeat 3]

Per ogni problema esegue nella sandbox, con EvalPlus 0.3.1 come il runner ma con
``fast_check=False``, la canonica sui test base e plus e stampa quanti test falliscono. Per i
primi test falliti rilancia la funzione senza limite di tempo e riporta: tempo misurato, limite
di EvalPlus (max(1 s, 4 x tempo della ground truth)), eccezione, confronto con l'output atteso.
Con ``--load N`` ripete la prova con N invocazioni in parallelo (carico simile ai job).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

from bench.config.builder import load_experiment
from bench.execution.sandbox import ApptainerSandbox
from bench.pipeline.stages.evalplus_groundtruth import groundtruth_ref, load_groundtruth
from bench.store.artifact_store import ArtifactStore

PROBE = r"""
import json, pickle, sys, time, os
os.environ["EVALPLUS_MAX_MEMORY_BYTES"] = str(4 * 1024**3)
from evalplus.eval import untrusted_check
from evalplus.eval._special_oracle import _poly
payload = pickle.load(open("/work/payload.pkl", "rb"))
p, o = payload["problem"], payload["oracle"]
code = p["prompt"] + p["canonical_solution"]
ns = {}
exec(code, ns)
fn = ns[p["entry_point"]]
report = {"task_id": p["task_id"], "atol": p["atol"]}
for part in ("base", "plus"):
    inputs, exp, ref = p[part + "_input"], o[part], o[part + "_time"]
    stat, details = untrusted_check(
        "humaneval" if p["task_id"].startswith("HumanEval") else "mbpp", code, inputs,
        p["entry_point"], expected=exp, atol=p["atol"], ref_time=ref, fast_check=False,
        min_time_limit=1.0, gt_time_limit_factor=4.0)
    failed = [i for i, ok in enumerate(details) if not ok]
    rows = []
    for i in failed[:3]:
        t0 = time.perf_counter()
        try:
            out = fn(*inputs[i]); err = None
        except BaseException as e:
            out, err = None, repr(e)[:200]
        dt = time.perf_counter() - t0
        if p["entry_point"] == "find_zero" and err is None:
            check = f"|poly(x)|={abs(_poly(*inputs[i], out)):.3g}"
        else:
            got, want = repr(out)[:80], repr(exp[i])[:80]
            check = "equal" if out == exp[i] else f"different: got {got} expected {want}"
        rows.append({"index": i, "seconds": round(dt, 4), "limit": round(max(1.0, 4 * ref[i]), 4),
                     "gt_seconds": round(ref[i], 4), "error": err, "check": check})
    report[part] = {"status": stat, "n": len(inputs), "n_checked": len(details),
                    "n_failed": len(failed), "first_failures": rows}
print(json.dumps(report))
"""


def run_one(box: ApptainerSandbox, root, payload: bytes) -> dict:  # type: ignore[no-untyped-def,type-arg]
    work = root / f"dbg_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        (work / "payload.pkl").write_bytes(payload)
        (work / "probe.py").write_text(PROBE, encoding="utf-8")
        res = box.run(["python3", "/work/probe.py"], work, 1800)
        if res.returncode != 0:
            return {"error": (res.stderr or res.stdout)[-1500:]}
        return json.loads(res.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("problems", nargs="+", help="problem keys, e.g. humaneval/32 mbpp/255")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--load", type=int, default=16)
    ns = parser.parse_args()

    cfg = load_experiment(["paths=cluster", "stage=execute"])
    box = ApptainerSandbox(cfg.execution)
    box.check()
    root = box.workdir_root(cfg.paths.tmp) / "debug_canonical"
    root.mkdir(parents=True, exist_ok=True)
    gt = load_groundtruth(ArtifactStore(cfg.paths.artifacts).read_table(groundtruth_ref()))
    by_key = {}
    for task_id, row in gt.items():
        family, number = task_id.split("/")
        by_key[f"{'humaneval' if family == 'HumanEval' else 'mbpp'}/{number}"] = row
    for key in ns.problems:
        payload = bytes(by_key[key]["payload"])
        print(f"===== {key}")
        for r in range(ns.repeat):
            print(f"-- alone, run {r + 1}: {json.dumps(run_one(box, root, payload))}")
        if ns.load > 1:
            with ThreadPoolExecutor(ns.load) as pool:
                jobs = [payload] * ns.load
                results = list(pool.map(lambda data: run_one(box, root, data), jobs))
            for part in ("base", "plus"):
                fails = [x.get(part, {}).get("n_failed") for x in results]
                print(f"-- under load x{ns.load}, {part} failed tests per run: {fails}")
            print(f"-- under load, first run: {json.dumps(results[0])}")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
