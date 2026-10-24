"""Pilota di T2.2(b) su C++ prima del lancio completo (condizione dell'utente del 24 ottobre 2026).

Uso (bench-core; vedi scripts/pilot_t2_2_cpp.sh, che lancia i job):
    python scripts/pilot_t2_2_cpp.py generate   # gpuq: T2.2 full_rewrite (classe dell'attacco)
    python scripts/pilot_t2_2_cpp.py execute    # defq: test di originale, attaccato, riparato
    python scripts/pilot_t2_2_cpp.py report     # nodo di login: classificazione e regola

Campioni: per ogni generatore e ogni problema di L1 dev C++ (HumanEvalPack) il primo campione della
baseline estratto correttamente (al più ``N_PROBLEMS`` problemi per modello, nell'ordine
``derive_seed(global_seed, "t2_2-cpp-pilot", problem_key)``). Ogni fallimento dell'attaccato si
classifica in:

- ``boilerplate``: errore di compilazione dovuto solo a righe di contorno tolte o cambiate
  (``#include``, ``using``, tipi dei parametri della funzione del problema). Il codice si
  **ripara** rimettendo le righe di contorno dell'originale e la sua intestazione della funzione; se
  il riparato compila l'errore era di contorno (``boilerplate_only`` se il riparato passa i test);
- ``other_compile``: errore di compilazione anche dopo la riparazione;
- ``runtime_or_logic``: compila ma fallisce i test, va in errore o in timeout.

**Regola fissata dall'utente prima del pilota:** se più del 10% dei campioni fallisce **solo** per
righe di contorno (``boilerplate_only``: il riparato passa i test), si prepara la v3 della full
rewrite; altrimenti si procede con la v2 e il fenomeno si dichiara come limite dell'attaccante.
Scrive solo in ``$WMB/tmp/t2_2_cpp_pilot/``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from bench.attacks.base import AttackContext, grid_attacks
from bench.attacks.llm import LocalLLM
from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.domain.ids import derive_seed
from bench.domain.models import CodeSample, Problem
from bench.execution.executors import EXECUTORS, SampleToRun, executor_name
from bench.execution.sandbox import make_sandbox
from bench.generation.code_extractor import _function_name
from bench.lang.parsers import ParserService, default_parsers, iter_nodes
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.store.artifact_store import ArtifactStore

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")
N_PROBLEMS = 100
LEVEL, LANG, SPLIT = "L1", Language.CPP, "dev"
THRESHOLD = 0.10
PREAMBLE = ("#include", "using ")


def _problems(store: ArtifactStore, global_seed: int) -> list[Problem]:
    table = store.read_table(problems_ref(LEVEL, str(LANG)))
    rows = table[table["split"] == SPLIT].to_dict(orient="records")
    problems = [Problem.model_validate(r) for r in rows]
    problems.sort(key=lambda p: derive_seed(global_seed, "t2_2-cpp-pilot", p.problem_key))
    return problems[:N_PROBLEMS]


def _attack() -> Any:
    attacks = [a for a in grid_attacks("T2.2").values() if a.params["variant"] == "full_rewrite"]
    return attacks[0]


def generate(cfg: Any, store: ArtifactStore, out: Path) -> None:
    attack = _attack()
    p = attack.params
    llm = LocalLLM(
        cfg.models_catalog[p["attacker"]],
        max_new_tokens=int(p["max_new_tokens"]),
        batch_size=int(p["batch_size"]),
    )
    problems = _problems(store, cfg.global_seed)
    rows: list[dict[str, Any]] = []
    for model_id in MODELS:
        base = store.read_table(baseline_ref(model_id, LEVEL, str(LANG), SPLIT))
        samples = [CodeSample.model_validate(r) for r in base.to_dict(orient="records")]
        chosen: list[tuple[Problem, CodeSample]] = []
        for problem in problems:
            found = sorted(
                (s for s in samples if s.problem_key == problem.problem_key and s.extraction_ok),
                key=lambda s: s.sample_index or 0,
            )
            if found:
                chosen.append((problem, found[0]))
        ctxs = [
            AttackContext(
                rng=random.Random(0),
                problem=problem,
                parsers=default_parsers(),
                sample_id=sample.sample_id,
                llm=llm,
            )
            for problem, sample in chosen
        ]
        outcomes = attack.apply_many([s.code for _, s in chosen], LANG, ctxs)
        for (problem, sample), result in zip(chosen, outcomes, strict=True):
            rows.append(
                {
                    "model_id": model_id,
                    "problem_key": problem.problem_key,
                    "sample_id": sample.sample_id,
                    "original": sample.code,
                    "attacked": result.code,
                    "attack_status": str(result.status),
                    "detail": result.detail,
                }
            )
        sys.stdout.write(f"{model_id}: {len(chosen)} samples\n")
    data = {"attack_params": p, "attacker": llm.info(), "rows": rows}
    (out / "generate.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    sys.stdout.write(f"wrote {out / 'generate.json'}\n")


def _entry_header(code: str, entry: str, parsers: ParserService) -> tuple[int, int, str] | None:
    """Byte di inizio e fine e testo dell'intestazione (fino al corpo) della funzione ``entry``."""
    data = code.encode("utf-8")
    for node in iter_nodes(parsers.parse(code, LANG).root_node):
        if node.type != "function_definition" or _function_name(node, LANG) != entry:
            continue
        body = node.child_by_field_name("body")
        if body is not None:
            return (
                node.start_byte,
                body.start_byte,
                data[node.start_byte : body.start_byte].decode(),
            )
    return None


def repair(original: str, attacked: str, entry: str, parsers: ParserService) -> str:
    """Attaccato con le righe di contorno e l'intestazione della funzione dell'originale."""
    code = attacked
    header_new = _entry_header(code, entry, parsers)
    header_old = _entry_header(original, entry, parsers)
    if header_new is not None and header_old is not None and header_new[2] != header_old[2]:
        data = code.encode("utf-8")
        start, end, _ = header_new
        code = (data[:start] + header_old[2].encode("utf-8") + data[end:]).decode("utf-8")
    lines = code.splitlines()
    present = {line.replace(" ", "") for line in lines}
    missing = [
        line
        for line in original.splitlines()
        if line.strip().startswith(PREAMBLE) and line.replace(" ", "") not in present
    ]
    includes = [m for m in missing if m.strip().startswith("#include")]
    usings = [m for m in missing if m.strip().startswith("using ")]
    # gli ``using`` dopo l'ultimo ``#include`` (lo spazio dei nomi deve essere già dichiarato)
    lines = includes + lines
    last = max(
        (i for i, line in enumerate(lines) if line.strip().startswith("#include")), default=-1
    )
    lines[last + 1 : last + 1] = usings
    return "\n".join(lines) + "\n" if missing else code


def _executor(cfg: Any, sandbox: Any, root: Path, dataset: str) -> Any:
    return EXECUTORS.get(executor_name(dataset))(cfg, sandbox, LANG, root)


def execute(cfg: Any, store: ArtifactStore, out: Path) -> None:
    data = json.loads((out / "generate.json").read_text(encoding="utf-8"))
    problems = {p.problem_key: p for p in _problems(store, cfg.global_seed)}
    sandbox = make_sandbox(cfg.execution)
    root = sandbox.workdir_root(cfg.paths.tmp / "t2_2_cpp_pilot")
    root.mkdir(parents=True, exist_ok=True)
    parsers = default_parsers()
    for row in data["rows"]:
        problem = problems[row["problem_key"]]
        row["repaired"] = repair(
            row["original"], row["attacked"], str(problem.entry_point), parsers
        )
    statuses: dict[str, dict[str, str]] = {}
    for key, problem in problems.items():
        executor = _executor(cfg, sandbox, root, problem.dataset)
        rows = [r for r in data["rows"] if r["problem_key"] == key]
        samples = [
            SampleToRun(f"{r['sample_id']}#{version}", key, 0, r[version])
            for r in rows
            for version in ("original", "attacked", "repaired")
        ]
        for record in executor.run_problem(problem, samples) if samples else []:
            sid, _, version = record.sample_id.partition("#")
            statuses.setdefault(sid, {})[version] = str(record.status)
    for row in data["rows"]:
        row["status"] = statuses.get(row["sample_id"], {})
    (out / "execute.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    sys.stdout.write(f"wrote {out / 'execute.json'}\n")


def classify(row: dict[str, Any]) -> str:
    """Categoria dell'attaccato (``passed`` se passa i test)."""
    st = row["status"]
    if row["attack_status"] == "TOOL_ERROR":
        return "attack_tool_error"  # estrazione fallita: codice originale
    if st.get("attacked") == "PASSED":
        return "passed"
    if st.get("attacked") == "COMPILE_ERROR":
        if row["repaired"] == row["attacked"] or st.get("repaired") == "COMPILE_ERROR":
            return "other_compile"
        return "boilerplate_only" if st.get("repaired") == "PASSED" else "boilerplate_then_fails"
    return "runtime_or_logic"


def report(cfg: Any, store: ArtifactStore, out: Path) -> None:
    data = json.loads((out / "execute.json").read_text(encoding="utf-8"))
    rows = data["rows"]
    w = sys.stdout.write
    w("##### Pilota T2.2(b) full_rewrite su L1 dev C++ (HumanEvalPack)\n")
    w(f"attack_params {json.dumps(data['attack_params'], sort_keys=True)}\n")
    w(f"attacker {json.dumps(data['attacker'], sort_keys=True)}\n")
    for model_id in (*MODELS, "all"):
        sel = [r for r in rows if model_id in ("all", r["model_id"])]
        n = len(sel)
        if not n:
            continue
        cats = Counter(classify(r) for r in sel)
        orig_pass = sum(r["status"].get("original") == "PASSED" for r in sel)
        w(f"\n=== {model_id}: {n} campioni\n")
        w(f"  attack_status {dict(Counter(r['attack_status'] for r in sel))}\n")
        w(f"  Pass@1 originale {orig_pass}/{n}, attaccato {cats['passed']}/{n}\n")
        w(f"  stati dell'attaccato {dict(Counter(r['status'].get('attacked') for r in sel))}\n")
        for cat in (
            "boilerplate_only", "boilerplate_then_fails", "other_compile", "runtime_or_logic",
            "attack_tool_error",
        ):  # fmt: skip
            k = cats[cat]
            both = sum(
                1 for r in sel if classify(r) == cat and r["status"].get("original") != "PASSED"
            )
            w(f"  {cat:<24} {k:>4}  ({k / n:.1%}; di cui {both} con l'originale già fallito)\n")
        if model_id == "all":
            share = cats["boilerplate_only"] / n
            verdict = "v3 della full rewrite" if share > THRESHOLD else "procedere con la v2"
            w(
                f"\nREGOLA: boilerplate_only {share:.1%} "
                f"{'>' if share > THRESHOLD else '<='} {THRESHOLD:.0%} -> {verdict}\n"
            )
    examples = [r for r in rows if classify(r) in ("boilerplate_only", "boilerplate_then_fails")]
    for r in examples[:3]:
        w(f"\n----- esempio {r['model_id']} {r['problem_key']} ({classify(r)})\n")
        w(f"--- originale\n{r['original']}\n--- attaccato\n{r['attacked']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["generate", "execute", "report"])
    args = parser.parse_args()
    stage = {"generate": "attack", "execute": "execute", "report": "metrics"}[args.mode]
    cfg = load_experiment(["paths=cluster", f"stage={stage}"])
    store = ArtifactStore(cfg.paths.artifacts)
    out = cfg.paths.tmp / "t2_2_cpp_pilot"
    out.mkdir(parents=True, exist_ok=True)
    {"generate": generate, "execute": execute, "report": report}[args.mode](cfg, store, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
