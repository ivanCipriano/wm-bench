"""Verifica della patch 0004 di MCGMark (outlier del paper, D32) su 20 prompt Python di L1 dev.

Uso (bench-core; vedi scripts/check_mcgmark_outliers.sh, che lancia i job):
    python scripts/check_mcgmark_outliers.py generate [--model qwen25_coder_7b]   # gpuq
    python scripts/check_mcgmark_outliers.py execute  [--model ...]               # defq, sandbox
    python scripts/check_mcgmark_outliers.py report   [--model ...]               # nodo di login

Decisione dell'utente del 21 ottobre 2026: la prova serve a verificare che la patch funzioni
(inserimento riuscito, messaggi recuperati, decodifica corretta); Pass@1 con e senza solo a titolo
informativo. Due varianti, stessi problemi, semi, messaggi e chiave del framework:
- ``off``: nessun iperparametro (codice ufficiale, con la 0004 applicata ma spenta). Il testo deve
  essere **identico carattere per carattere** a quello dei campioni marcati di L1 dev congelati
  (generati con 0000-0003, stesso seme);
- ``on``: ``outlier_scale`` = 1,5 (valore del codice e di Tukey).
I 20 problemi sono i primi di L1 dev Python nell'ordine
``derive_seed(global_seed, "mcgmark-outliers-check", problem_key)``, 6 campioni ciascuno (n di L1).
Scrive solo in ``$WMB/tmp/mcgmark_outliers/<modello>/``: niente artefatti, nessuna metrica.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.domain.ids import derive_seed
from bench.domain.models import Problem
from bench.execution.executors import EXECUTORS, EvalPlusExecutor, SampleToRun, executor_name
from bench.execution.sandbox import make_sandbox
from bench.generation.decoding import neutral_settings
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.base import DetectInput, Detector, PromptEmbedder
from bench.pipeline.stage import Cell
from bench.pipeline.stages.evalplus_groundtruth import groundtruth_ref, load_groundtruth
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import decoding_for, problems_ref
from bench.pipeline.stages.watermark import KEY_ID, make_adapter, watermarked_ref
from bench.store.artifact_store import ArtifactStore

METHOD = "mcgmark"
N_PROBLEMS = 20
VARIANTS: dict[str, dict[str, Any]] = {"off": {}, "on": {"outlier_scale": 1.5}}
ROUND, INFO = 24, 12


def _problems(store: ArtifactStore, global_seed: int) -> list[Problem]:
    table = store.read_table(problems_ref("L1", "python"))
    problems = [
        Problem.model_validate(r) for r in table[table["split"] == "dev"].to_dict(orient="records")
    ]
    problems.sort(key=lambda p: derive_seed(global_seed, "mcgmark-outliers-check", p.problem_key))
    return sorted(problems[:N_PROBLEMS], key=lambda p: p.problem_key)


def generate(cfg: Any, store: ArtifactStore, out: Path, model_id: str) -> None:
    adapter = make_adapter(cfg, METHOD)
    assert isinstance(adapter, PromptEmbedder) and isinstance(adapter, Detector)
    model = cfg.models_catalog[model_id]
    decoding = decoding_for(cfg, "L1")
    request_decoding = {**neutral_settings(decoding), "torch_dtype": model.dtype}
    prompts = PromptBuilder.from_config(cfg.prompt)
    problems = _problems(store, cfg.global_seed)
    by_key = {p.problem_key: p for p in problems}
    for variant, hp in VARIANTS.items():
        run_dir = out / f"run_{variant}"
        run = adapter.embed_from_prompts(
            problems, hp, model, KEY_ID, request_decoding, decoding.n, prompts, run_dir
        )
        extras = {r.item_id: r.extra or {} for r in (run.worker.results if run.worker else [])}
        inputs = [
            DetectInput(
                item_id=s.sample_id,
                language="python",
                code=s.code,
                prompt_messages=prompts.build(by_key[s.problem_key]),
                expected_message=s.expected_message,
            )
            for s in run.samples
        ]
        detected = adapter.detect_codes(
            inputs, hp, model, KEY_ID, request_decoding, out / f"detect_{variant}"
        )
        rows = []
        for s, d in zip(run.samples, detected, strict=True):
            extra = extras.get(f"{s.problem_key}#{s.sample_index}", {})
            rows.append(
                {
                    "sample_id": s.sample_id,
                    "problem_key": s.problem_key,
                    "sample_index": s.sample_index,
                    "seed": s.seed,
                    "raw_output": s.raw_output,
                    "code": s.code,
                    "embed_status": s.embed_status,
                    "message": s.expected_message,
                    "embedded_bits": extra.get("embedded_bits", ""),
                    "outliers": extra.get("outliers", []),
                    "detect_status": str(d.status),
                    "detect_bits": (d.extra or {}).get("bits", ""),
                    "rounds": (d.extra or {}).get("rounds", []),
                    "score": d.score,
                }
            )
        path = out / f"{variant}.json"
        path.write_text(
            json.dumps({"hp": hp, "config_hash": adapter.config_hash(hp), "rows": rows}, indent=1),
            encoding="utf-8",
        )
        sys.stdout.write(f"wrote {path} ({len(rows)} samples)\n")


def execute(cfg: Any, store: ArtifactStore, out: Path, model_id: str) -> None:
    problems = {p.problem_key: p for p in _problems(store, cfg.global_seed)}
    sandbox = make_sandbox(cfg.execution)  # come la fase execute (controllo dell'immagine)
    root = sandbox.workdir_root(cfg.paths.tmp / "check_mcgmark_outliers")
    root.mkdir(parents=True, exist_ok=True)
    groundtruth = load_groundtruth(store.read_table(groundtruth_ref()))
    executors: dict[str, Any] = {}
    for dataset in sorted({p.dataset for p in problems.values()}):
        cls = EXECUTORS.get(executor_name(dataset))
        executors[dataset] = (
            EvalPlusExecutor(cfg, sandbox, Language.PYTHON, root, groundtruth)
            if cls is EvalPlusExecutor
            else cls(cfg, sandbox, Language.PYTHON, root)
        )
    for variant in VARIANTS:
        data = json.loads((out / f"{variant}.json").read_text(encoding="utf-8"))
        status: dict[str, str] = {}
        for key, problem in problems.items():
            samples = [
                SampleToRun(r["sample_id"], key, r["sample_index"], r["code"])
                for r in data["rows"]
                if r["problem_key"] == key
            ]
            for record in executors[problem.dataset].run_problem(problem, samples):
                status[record.sample_id] = str(record.status)
        path = out / f"{variant}_execution.json"
        path.write_text(json.dumps(status, indent=1), encoding="utf-8")
        passed = sum(s == "PASSED" for s in status.values())
        sys.stdout.write(f"{variant}: Pass@1 {passed}/{len(status)}\n")


def _positions(row: dict[str, Any]) -> tuple[int, int]:
    """Bit d'informazione letti dal codice diversi da quelli inseriti, e quanti di questi sono su
    posizioni in cui il token scelto era un outlier spostato (li corregge il bit di correzione)."""
    embedded, read = row["embedded_bits"], row["detect_bits"]
    wrong = [
        i
        for i in range(min(len(embedded), len(read)))
        if i % ROUND < INFO and embedded[i] != read[i]
    ]
    hits = {o["position"] for o in row["outliers"] if o.get("sampled_moved")}
    return len(wrong), sum(1 for i in wrong if i in hits)


def _passed(status: dict[str, str], ids: list[str]) -> str:
    return f"{sum(status.get(i) == 'PASSED' for i in ids)}/{len(ids)}"


def report(cfg: Any, store: ArtifactStore, out: Path, model_id: str) -> None:
    adapter = make_adapter(cfg, METHOD)
    frozen_hash = adapter.config_hash({})
    frozen = store.read_table(watermarked_ref(METHOD, model_id, frozen_hash, "L1", "python", "dev"))
    frozen_text = dict(zip(frozen["sample_id"], frozen["raw_output"], strict=True))
    cell = Cell(
        source="llm_watermarked",
        method=METHOD,
        model_id=model_id,
        level="L1",
        language="python",
        split="dev",
    )
    frozen_exec = store.read_table(execution_ref(cell, frozen_hash))
    frozen_status = dict(zip(frozen_exec["sample_id"], frozen_exec["status"], strict=True))
    w = sys.stdout.write
    w(f"##### MCGMark, patch 0004: {N_PROBLEMS} prompt di L1 dev Python, {model_id}\n")
    for variant in VARIANTS:
        data = json.loads((out / f"{variant}.json").read_text(encoding="utf-8"))
        rows = data["rows"]
        ids = [r["sample_id"] for r in rows]
        ok = [r for r in rows if r["embed_status"] == "OK"]
        recovered = [r for r in ok if r["rounds"] and r["rounds"][0] == r["message"]]
        wrong = [_positions(r) for r in ok]
        n_pos = mean(len(r["embedded_bits"]) for r in rows)
        w(f"\n=== {variant} {data['hp']} (config {data['config_hash']}), {len(rows)} campioni\n")
        w(f"  inserimento: {dict(Counter(r['embed_status'] for r in rows))}\n")
        w(f"  inserimento riuscito: {len(ok)}/{len(rows)}; posizioni marcate, media {n_pos:.1f}\n")
        w(f"  rilevazione sul codice: {dict(Counter(r['detect_status'] for r in rows))}\n")
        w(f"  messaggio recuperato (primo ciclo = atteso), sugli OK: {len(recovered)}/{len(ok)}\n")
        w(
            f"  bit d'informazione letti diversi dagli inseriti (OK): {sum(a for a, _ in wrong)}"
            f", su outlier spostati e scelti: {sum(b for _, b in wrong)}\n"
        )
        info = [o for r in rows for o in r["outliers"]]
        if info:
            w(
                f"  posizioni d'informazione {len(info)}; per posizione, media: outlier "
                f"{mean(o['n_outliers'] for o in info):.1f}, inclusi "
                f"{mean(o['n_included'] for o in info):.1f}, spostati "
                f"{mean(o['n_moved'] for o in info):.1f}; token scelto fra gli spostati: "
                f"{sum(bool(o.get('sampled_moved')) for o in info)}\n"
            )
        if variant == "off":
            same = sum(frozen_text.get(r["sample_id"]) == r["raw_output"] for r in rows)
            w(f"  testo identico ai campioni congelati (0000-0003): {same}/{len(rows)}\n")
            w(f"  Pass@1 dei campioni congelati (stessi id): {_passed(frozen_status, ids)}\n")
        exec_path = out / f"{variant}_execution.json"
        if exec_path.is_file():
            status = json.loads(exec_path.read_text(encoding="utf-8"))
            w(f"  Pass@1 (informativo): {_passed(status, ids)}\n")


def detail(cfg: Any, store: ArtifactStore, out: Path, model_id: str) -> None:
    """Campioni OK: posizioni inserite e rilevate, messaggio, bit diversi per metà del ciclo."""
    w = sys.stdout.write
    for variant in VARIANTS:
        rows = json.loads((out / f"{variant}.json").read_text(encoding="utf-8"))["rows"]
        w(f"\n=== {variant}: campioni con inserimento OK\n")
        for r in (r for r in rows if r["embed_status"] == "OK"):
            emb, read = r["embedded_bits"], r["detect_bits"]
            n = min(len(emb), len(read))
            diff = [i for i in range(n) if emb[i] != read[i]]
            hits = {o["position"] for o in r["outliers"] if o.get("sampled_moved")}
            info = [i for i in diff if i % ROUND < INFO]
            corr = [i for i in diff if i % ROUND >= INFO]
            first = r["rounds"][0] if r["rounds"] else None
            matches = (
                sum(a == b for a, b in zip(first, r["message"], strict=True)) if first else None
            )
            w(
                f"  {r['sample_id']}: posizioni inserite {len(emb)}, rilevate {len(read)}, "
                f"primo ciclo {first} contro {r['message']} ({matches}/12); bit diversi: "
                f"informazione {len(info)} (su outlier spostati e scelti "
                f"{sum(i in hits for i in info)}), correzione {len(corr)}; prima differenza "
                f"{diff[0] if diff else None}\n"
            )
            if first != r["message"]:
                w(f"    inseriti {emb[: 2 * ROUND]}\n    letti    {read[: 2 * ROUND]}\n")
                log = [o for o in r["outliers"] if o["position"] < ROUND]
                w(f"    registro degli outlier del primo ciclo: {log}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["generate", "execute", "report", "detail"])
    parser.add_argument("--model", default="qwen25_coder_7b")
    args = parser.parse_args()
    stage = {
        "generate": "watermark",
        "execute": "execute",
        "report": "metrics",
        "detail": "metrics",
    }[args.mode]
    cfg = load_experiment(["paths=cluster", f"stage={stage}"])
    store = ArtifactStore(cfg.paths.artifacts)
    out = cfg.paths.tmp / "mcgmark_outliers" / args.model
    out.mkdir(parents=True, exist_ok=True)
    {"generate": generate, "execute": execute, "report": report, "detail": detail}[args.mode](
        cfg, store, out, args.model
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
