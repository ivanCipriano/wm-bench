"""Funzione d'ingresso rinominata nelle riscritture con LLM: T3.1 contro T2.2(a) e token verdi dei
nomi con STONE (decisioni dell'utente del 28 ottobre 2026; ``bench.attacks.interface``).

Uso (con bench-core):
    python scripts/t3_1_renames.py [METHOD ...]           # nodo di login, pochi minuti
    LOG=<file nuovo> bash scripts/t3_1_renames.sh         # con --greens, job GPU (STONE)

Per ogni metodo congelato in generazione (default: stone, mcgmark, promptmark; SWEET quando sarà
congelato) e cella di L1 test, per T3.1 (k2) e per T2.2(a) (``variant=conservative``: stesso
prompt, attaccante Llama senza watermark):

- campioni ``APPLIED`` con la funzione d'ingresso rinominata o mancante, e la frazione;
- fallimenti di Pass@1 divisi fra funzione rinominata o mancante e altri.

Con ``--greens`` (solo STONE, su un nodo NVIDIA: la green list si genera con ``torch.randperm``
sul dispositivo della rilevazione): per ogni campione rinominato, frazione di token verdi per k2 e
per k1 nel nome originale e nel nome nuovo (tokenizzati con lo spazio che li precede nel codice) e
se il primo token è verde. Con ``prefix_length = 0`` (FIXED_NATIVE di STONE) la green list non
dipende dal contesto: è una per chiave, quindi i due nomi si confrontano sulla stessa lista. Il
calcolo gira nell'ambiente ``stone`` (``scripts/stone_name_greens.py``) con lo shim del framework.
Atteso senza effetto del watermark: frazione ≈ γ per entrambi i nomi; T2.2 fa da controllo.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from bench_contracts.enums import WorkerOp

from bench.attacks.base import grid_attacks
from bench.attacks.interface import MISSING, RENAMED, entry_status, pass_failures
from bench.config.builder import load_experiment, repo_root
from bench.domain.models import Problem
from bench.hpo.freeze import load_freeze
from bench.hpo.grid import resolve
from bench.lang.parsers import default_parsers
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import problems_ref
from bench.pipeline.stages.watermark import make_adapter, watermarked_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
METHODS = ("stone", "mcgmark", "promptmark", "sweet")
ATTACKS = (("T3.1", {"key_id": "k2"}), ("T2.2", {"variant": "conservative"}))
KEYS = ("k1", "k2")


def problems_of(store: ArtifactStore, lang: str, cache: dict[str, Any]) -> dict[str, Problem]:
    if lang not in cache:
        cache[lang] = {
            str(r["problem_key"]): Problem.model_validate(r)
            for r in store.read_table(problems_ref(LEVEL, lang)).to_dict(orient="records")
        }
    return dict(cache[lang])


def cell_renames(
    store: ArtifactStore,
    method: str,
    cell: Any,
    attack_id: str,
    attack: Any,
    cache: dict[str, Any],
) -> tuple[str, list[dict[str, str]]] | None:
    """Riga del report e campioni rinominati (nome originale e nuovo) di una cella."""
    model, lang, h = cell.model_id, cell.language, cell.config_hash
    ph = attack.params_hash(lang)
    ref = attacked_ref(attack_id, ph, method, model, h, LEVEL, lang, SPLIT)
    if not store.exists(ref):
        return None
    samples = store.read_table(ref)
    problems = problems_of(store, lang, cache)
    wm = store.read_table(watermarked_ref(method, model, h, LEVEL, lang, SPLIT))
    parent = {str(k): str(v) for k, v in zip(wm["sample_id"], wm["code"], strict=True)}
    parsers = default_parsers()
    rows = samples.to_dict(orient="records")
    applied = [r for r in rows if str(r["attack_status"]) == "APPLIED"]
    counts = {RENAMED: 0, MISSING: 0}
    pairs = []
    for r in applied:
        problem = problems[str(r["problem_key"])]
        got = entry_status(
            str(r["code"] or ""), problem, parent.get(str(r["parent_id"]), ""), parsers
        )
        if got.status in counts:
            counts[got.status] += 1
        if got.status == RENAMED and got.new_name and problem.entry_point:
            pairs.append({"original": problem.entry_point, "new": got.new_name})
    lost = counts[RENAMED] + counts[MISSING]
    line = (
        f"  {attack_id:<5} {model:<20} {lang:<7} APPLIED {len(applied):>5}  renamed "
        f"{counts[RENAMED]:>4}  missing {counts[MISSING]:>4}  "
        f"lost {lost / max(1, len(applied)):.4f}"
    )
    exe_ref = execution_ref(
        Cell(
            method=method, model_id=model, level=LEVEL, language=lang, split=SPLIT,
            attack_id=attack_id, attack_params_hash=ph, source="attacked",
        ),
        h,
    )  # fmt: skip
    if store.exists(exe_ref):
        runs = store.read_table(exe_ref)
        status = {str(k): str(v) for k, v in zip(runs["sample_id"], runs["status"], strict=True)}
        f = pass_failures(rows, status, problems, parsers)
        line += (
            f"  | pass@1 failures {f['failed']}/{f['n']}: renamed {f['renamed']}, "
            f"missing {f['missing']}, other {f['other']}"
        )
    return line, pairs


def greens(
    cfg: Any, store: ArtifactStore, cells: dict[tuple[str, str, str], list[dict[str, str]]]
) -> None:
    """Token verdi dei nomi (STONE) nell'ambiente ``stone``, con lo shim del framework."""
    adapter = make_adapter(cfg, "stone")
    work = Path(cfg.paths.tmp) / "t3_1_renames"
    work.mkdir(parents=True, exist_ok=True)
    payload = []
    for (model, lang, h), pairs in sorted(cells.items()):
        hp = resolve(cfg, adapter, h)[0]
        spec = cfg.models_catalog[model]
        requests = {
            k: adapter.build_request(
                WorkerOp.DETECT, spec, hp, k, {}, "", work / f"{model}_{lang}_{k}", "cuda:0"
            ).to_dict()
            for k in KEYS
        }
        payload.append(
            {"model": model, "language": lang, "config_hash": h, "requests": requests,
             "gamma": float(hp["gamma"]), "pairs": pairs}
        )  # fmt: skip
    inp, out = work / "input.json", work / "output.json"
    inp.write_text(json.dumps(payload), encoding="utf-8")
    python = adapter.worker._python(adapter.env_name)
    env = adapter.worker.environment(adapter.source())
    script = repo_root() / "scripts" / "stone_name_greens.py"
    subprocess.run(
        [str(python), str(script), "--input", str(inp), "--output", str(out)], env=env, check=True
    )
    write_greens(json.loads(out.read_text(encoding="utf-8")))


def write_greens(results: list[dict[str, Any]]) -> None:
    sys.stdout.write("\n##### STONE: token verdi nei nomi dei campioni rinominati\n")
    for cell in results:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in cell["pairs"]:
            groups[p["attack"]].append(p)
        for attack_id, pairs in sorted(groups.items()):
            sys.stdout.write(
                f"\n  {attack_id} {cell['model']} {cell['language']} (gamma {cell['gamma']}): "
                f"{len(pairs)} renamed\n"
            )
            for k in KEYS:
                parts = []
                for which in ("original", "new"):
                    frac = [p[f"{which}_{k}_green"] / max(1, p[f"{which}_tokens"]) for p in pairs]
                    first = [p[f"{which}_{k}_first"] for p in pairs if p[f"{which}_{k}_first"]
                             is not None]  # fmt: skip
                    parts.append(
                        f"{which} green fraction {mean(frac) if frac else float('nan'):.3f} "
                        f"first token green {mean(first) if first else float('nan'):.3f}"
                    )
                higher = sum(
                    p[f"new_{k}_green"] / max(1, p["new_tokens"])
                    > p[f"original_{k}_green"] / max(1, p["original_tokens"])
                    for p in pairs
                )
                sys.stdout.write(
                    f"    {k}: {'; '.join(parts)}; new > original {higher}/{len(pairs)}\n"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("methods", nargs="*", help=f"fra {', '.join(METHODS)}")
    parser.add_argument("--greens", action="store_true", help="token verdi dei nomi (STONE, GPU)")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    cache: dict[str, Any] = {}
    stone_pairs: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for method in args.methods or METHODS[:3]:
        record = load_freeze(store, method)
        if record is None:
            sys.stdout.write(f"\n##### {method}: NOT FROZEN\n")
            continue
        sys.stdout.write(f"\n##### {method}: funzione d'ingresso nei campioni APPLIED\n")
        for cell in record.cells:
            if cell.not_applicable or cell.config_hash is None:
                continue
            for attack_id, wanted in ATTACKS:
                for attack in grid_attacks(attack_id).values():
                    if any(str(attack.params.get(k)) != v for k, v in wanted.items()):
                        continue
                    if not attack.supports(cell.language):
                        continue
                    got = cell_renames(store, method, cell, attack_id, attack, cache)
                    if got is None:
                        sys.stdout.write(f"  {attack_id:<5} {cell.model_id} {cell.language}: "
                                         "attacked MISSING\n")  # fmt: skip
                        continue
                    line, pairs = got
                    sys.stdout.write(line + "\n")
                    if method == "stone":
                        key = (cell.model_id, cell.language, str(cell.config_hash))
                        stone_pairs[key].extend({**p, "attack": attack_id} for p in pairs)
    if args.greens:
        greens(cfg, store, stone_pairs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
