"""Pilota di ClassEval prima della generazione di L3 (decisione dell'utente del 29 ottobre 2026).

Uso (bench-core; vedi scripts/pilot_classeval.sh, che lancia i job):
    python scripts/pilot_classeval.py generate   # gpuq: baseline delle 10 classi, 2 modelli
    python scripts/pilot_classeval.py execute    # defq: test ufficiali (immagine sandbox_l3)
    python scripts/pilot_classeval.py report     # nodo di login: troncamento e classi risolte

Classi: le prime ``N_CLASSES`` delle 94 di L3 nell'ordine ``derive_seed(global_seed,
"classeval-pilot", problem_key)``. Generazione identica a quella della baseline (template
``classeval_python.j2``, system prompt, decoding ``level234``: 6 campioni, 1.024 token nuovi, stesso
seme per problema), con in più il numero di token generati: una risposta è **troncata** se non
contiene il token di fine sequenza entro il limite. Il report dà, per modello e in totale, le
risposte troncate, i campioni e le classi che passano i test e l'esito delle canoniche.

**Regola fissata dall'utente prima del pilota:** se più del 5% delle risposte è troncato ci si
ferma e si propone una soluzione prima del lancio. Il primo pilota (1.024 token) l'ha superata
(7,5%): da allora ClassEval ha il decoding ``classeval`` con 2.048 token (D40) e il pilota si ripete
sulle stesse classi. Scrive solo in ``$WMB/tmp/classeval_pilot_<max_new_tokens>/`` (il primo
pilota è in ``$WMB/tmp/classeval_pilot/``).
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment
from bench.data.loaders.classeval import ClassEvalLoader
from bench.domain.enums import ExecStatus, Language, Split
from bench.domain.ids import derive_seed
from bench.domain.models import Problem
from bench.execution.executors import ClassEvalExecutor, SampleToRun
from bench.execution.sandbox import make_sandbox
from bench.generation.code_extractor import FencedCodeExtractor
from bench.generation.hf_generator import HFTextGenerator, generation_seed, sample_sequences
from bench.generation.prompt_builder import PromptBuilder
from bench.pipeline.stages.generate_baseline import decoding_for

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")
N_CLASSES = 10
N_ALL = 1000  # per le canoniche: tutte le classi di L3
MAX_TRUNCATED = 0.05
L3_IMAGE = "sandbox_l3"


def _problems(cfg: Any, n: int = N_CLASSES) -> list[Problem]:
    loader = ClassEvalLoader(cfg.datasets["classeval"])
    problems = [
        p.model_copy(update={"split": Split.TEST}) for p in loader.load_problems(Language.PYTHON)
    ]
    problems.sort(key=lambda p: derive_seed(cfg.global_seed, "classeval-pilot", p.problem_key))
    return problems[:n]


def _eos_ids(generator: HFTextGenerator) -> set[int]:
    eos = generator.generation_config.eos_token_id
    return set(eos if isinstance(eos, list) else [eos]) - {None}


def generate(cfg: Any, out: Path) -> None:
    import torch

    decoding = decoding_for(cfg, "L3", "classeval")
    prompts = PromptBuilder.from_config(cfg.prompt)
    extractor = FencedCodeExtractor()
    problems = _problems(cfg)
    rows: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    for model_id in MODELS:
        generator = HFTextGenerator(cfg.models_catalog[model_id], decoding)
        eos = _eos_ids(generator)
        info[model_id] = generator.info()
        for problem in problems:
            seed = generation_seed(cfg.global_seed, model_id, problem.problem_key, problem.language)
            input_ids = generator.tokenizer.apply_chat_template(
                prompts.build(problem), add_generation_prompt=True, return_tensors="pt"
            ).to(generator.device)
            new = sample_sequences(
                generator.model, input_ids, generator.generation_config, decoding.n, seed
            )
            texts = generator.tokenizer.batch_decode(new, skip_special_tokens=True)
            for index, (tokens, raw) in enumerate(zip(new.tolist(), texts, strict=True)):
                ends = [i for i, t in enumerate(tokens) if t in eos]
                code, ok = extractor.extract(str(raw), problem)
                rows.append(
                    {
                        "model_id": model_id,
                        "problem_key": problem.problem_key,
                        "sample_index": index,
                        "prompt_tokens": int(input_ids.shape[1]),
                        "new_tokens": ends[0] + 1 if ends else len(tokens),
                        "truncated": not ends,
                        "raw_output": str(raw),
                        "code": code,
                        "extraction_ok": ok,
                    }
                )
            sys.stdout.write(f"{model_id} {problem.problem_key}: done\n")
        del generator
        gc.collect()
        torch.cuda.empty_cache()
    data = {
        "decoding": decoding.model_dump(mode="json"),
        "prompt_hashes": prompts.prompt_hashes(),
        "models": info,
        "rows": rows,
    }
    (out / "generate.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    sys.stdout.write(f"wrote {out / 'generate.json'}\n")


def l3_config(cfg: Any) -> Any:
    """Configurazione con l'immagine della sandbox di L3 (ADR-016)."""
    containers = cfg.paths.wmb / "containers"
    execution = cfg.execution.model_copy(
        update={
            "image_sif": containers / f"{L3_IMAGE}.sif",
            "image_dir": containers / f"{L3_IMAGE}_dir",
        }
    )
    return cfg.model_copy(update={"execution": execution})


def execute(cfg: Any, out: Path) -> None:
    data = json.loads((out / "generate.json").read_text(encoding="utf-8"))
    cfg = l3_config(cfg)
    sandbox = make_sandbox(cfg.execution)
    root = sandbox.workdir_root(out)
    root.mkdir(parents=True, exist_ok=True)
    executor = ClassEvalExecutor(cfg, sandbox, Language.PYTHON, root)
    results: dict[str, dict[str, Any]] = {}
    pilot = {p.problem_key for p in _problems(cfg)}
    for problem in _problems(cfg, N_ALL):  # canoniche di tutte le classi, campioni del pilota
        rows = [r for r in data["rows"] if r["problem_key"] == problem.problem_key]
        if problem.problem_key not in pilot:
            rows = []
        samples = [
            SampleToRun(f"{r['model_id']}#{r['sample_index']}", problem.problem_key,
                        r["sample_index"], r["code"], bool(r["extraction_ok"]))
            for r in rows
        ]  # fmt: skip
        samples += [SampleToRun(f"canonical#{r}", problem.problem_key, None,
                                executor.canonical(problem))
                    for r in range(cfg.execution.canonical_repeats)]  # fmt: skip
        for record in executor.run_problem(problem, samples):
            results[f"{problem.problem_key}|{record.sample_id}"] = {
                "status": str(record.status),
                "stderr_tail": record.stderr_tail,
            }
    data["execution"] = results
    data["sandbox_image_hash"] = sandbox.image_hash
    data["sandbox_versions"] = executor.last_versions
    (out / "execute.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    sys.stdout.write(f"wrote {out / 'execute.json'}\n")


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} = {100 * k / n:.1f}%" if n else "0/0"


def _model_report(
    model_id: str, rows: list[dict[str, Any]], results: dict[str, Any], n: int
) -> None:
    w = sys.stdout.write
    mine = [r for r in rows if r["model_id"] == model_id]
    tokens = sorted(r["new_tokens"] for r in mine)
    truncated = sum(r["truncated"] for r in mine)
    p90 = tokens[int(0.9 * (len(tokens) - 1))]
    w(f"\n## {model_id}\n")
    w(f"  truncated (no EOS within the limit): {_pct(truncated, len(mine))}\n")
    w(f"  new tokens: median {statistics.median(tokens):.0f}, p90 {p90}, max {tokens[-1]}\n")
    w(f"  extraction ok: {_pct(sum(r['extraction_ok'] for r in mine), len(mine))}\n")
    if not results:
        return
    status = {
        (r["problem_key"], r["sample_index"]): str(
            results[f"{r['problem_key']}|{model_id}#{r['sample_index']}"]["status"]
        )
        for r in mine
    }
    passed = [k for k, s in status.items() if s == ExecStatus.PASSED]
    keys = sorted({r["problem_key"] for r in mine})
    solved = [k for k in keys if any(p[0] == k for p in passed)]
    w(f"  samples passing all tests: {_pct(len(passed), len(status))}\n")
    w(f"  classes with at least one passing sample: {_pct(len(solved), len(keys))}\n")
    w(f"  statuses: {dict(sorted(Counter(status.values()).items()))}\n")
    trunc_fail = sum(
        1
        for r in mine
        if r["truncated"] and status[(r["problem_key"], r["sample_index"])] != ExecStatus.PASSED
    )
    w(f"  truncated and not passing: {trunc_fail}\n")
    for key in keys:
        line = " ".join(status[(key, i)][:4] for i in range(n))
        n_tr = sum(r["truncated"] for r in mine if r["problem_key"] == key)
        w(f"    {key:<26} {line}  truncated {n_tr}\n")


def _canonical_report(results: dict[str, Any]) -> None:
    w = sys.stdout.write
    canon: dict[str, list[dict[str, Any]]] = {}
    for k, v in sorted(results.items()):
        if "|canonical#" in k:
            canon.setdefault(k.split("|")[0], []).append(v)
    ok = [k for k, vs in canon.items() if all(v["status"] == ExecStatus.PASSED for v in vs)]
    w(f"\n## canonical solutions passing every repeat (all classes): {_pct(len(ok), len(canon))}\n")
    for key, vs in canon.items():
        if key not in ok:
            failed = next(v for v in vs if v["status"] != ExecStatus.PASSED)
            tail = (failed["stderr_tail"] or "")[-400:]
            w(f"  {key}: {[v['status'] for v in vs]}: {tail!r}\n")


def report(out: Path) -> None:
    path = out / "execute.json"
    data = json.loads((path if path.is_file() else out / "generate.json").read_text("utf-8"))
    rows, results = data["rows"], data.get("execution", {})
    decoding = data["decoding"]
    w = sys.stdout.write
    w(f"##### ClassEval pilot: {N_CLASSES} classes x {len(MODELS)} models x {decoding['n']} "
      f"samples, max_new_tokens {decoding['max_new_tokens']}\n")  # fmt: skip
    if results:
        packages = data.get("sandbox_versions", {}).get("classeval_packages", "?")
        w(f"sandbox image {data.get('sandbox_image_hash', '?')[:16]}, packages {packages}\n")
    for model_id in MODELS:
        _model_report(model_id, rows, results, int(decoding["n"]))
    if results:
        _canonical_report(results)
    total = sum(r["truncated"] for r in rows)
    share = total / len(rows) if rows else 0.0
    verdict = "STOP: propose a fix" if share > MAX_TRUNCATED else "OK"
    w(f"\n## truncated overall: {_pct(total, len(rows))}; rule: stop if > "
      f"{100 * MAX_TRUNCATED:.0f}% -> {verdict}\n")  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["generate", "execute", "report"])
    args = parser.parse_args()
    stage = {"generate": "generate_baseline", "execute": "execute", "report": "metrics"}[args.mode]
    cfg = load_experiment(["paths=cluster", f"stage={stage}"])
    tokens = decoding_for(cfg, "L3", "classeval").max_new_tokens
    out = cfg.paths.tmp / f"classeval_pilot_{tokens}"
    out.mkdir(parents=True, exist_ok=True)
    if args.mode == "generate":
        generate(cfg, out)
    elif args.mode == "execute":
        execute(cfg, out)
    else:
        report(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
