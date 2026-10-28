"""Diagnostica di T3.1 per un metodo in generazione (ADR-014 §3; M11): tempo reale della
ri-marcatura e cause dei campioni che non compilano più o vanno in ``TOOL_ERROR``.

Uso (con bench-core, nodo di login, nessun job; pochi minuti):
    python scripts/diag_t3_1.py stone | tee docs/milestone_logs/M11_t3_1_diag_stone.txt
    (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)

Per ogni cella congelata (L1 test):

- **tempo**: somma di ``elapsed_s`` dei risultati del worker della fase attack (da confrontare con
  ``M11_t3_1_cost.txt``) e numero di risultati;
- **uscite del generatore**: lunghezza in caratteri dell'uscita grezza, uscite con una fence di
  codice aperta e mai chiusa (indizio di troncamento a ``max_new_tokens``), stato d'inserimento;
- **TOOL_ERROR**: errori più frequenti;
- **campioni APPLIED che non compilano** (``COMPILE_ERROR``/``SYNTAX_ERROR``): quanti compilavano
  già da marcati, quanti hanno la fence aperta, rapporto di lunghezza attaccato/marcato e prima
  riga più frequente di ``stderr_tail``; due esempi con l'id del campione.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from statistics import median
from typing import Any

from bench.attacks.base import grid_attacks
from bench.config.builder import load_experiment
from bench.hpo.freeze import load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.watermark import watermarked_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT, ATTACK = "L1", "test", "T3.1"
NOT_COMPILING = ("COMPILE_ERROR", "SYNTAX_ERROR")
FENCE = "```"


def results(path: Path) -> list[dict[str, Any]]:
    """Righe di ``results.jsonl`` del worker (l'ultima per item, per le riprese)."""
    if not path.is_file():
        return []
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[str(row.get("item_id"))] = row
    return list(rows.values())


def open_fence(raw: str | None) -> bool:
    """Una fence aperta e mai chiusa: l'uscita si è fermata dentro il blocco di codice."""
    return raw is not None and raw.count(FENCE) % 2 == 1


def first_line(text: Any) -> str:
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    return lines[0][:120] if lines else "<empty>"


def worker_summary(raw: dict[str, Any], res: list[dict[str, Any]], secs: float) -> None:
    lengths = [len(v) for v in raw.values() if v]
    sys.stdout.write(
        f"  worker: {len(res)} results, {secs / 3600:.2f} h (sum of elapsed_s)\n"
        f"  embed status {dict(Counter(str(r.get('status')) for r in res))}\n"
        f"  raw output chars median {median(lengths) if lengths else 0:.0f} "
        f"max {max(lengths, default=0)}; open fence "
        f"{sum(open_fence(v) for v in raw.values())}/{len(raw)}\n"
    )


def tool_errors(samples: Any) -> None:
    errors = samples[samples["attack_status"] == "TOOL_ERROR"]
    if len(errors):
        why = Counter(
            str(json.loads(d or "{}").get("error", d))[:80] for d in errors["attack_detail"]
        )
        sys.stdout.write(f"  TOOL_ERROR {len(errors)}: {dict(why.most_common(5))}\n")


def not_compiling(
    store: ArtifactStore, samples: Any, raw: dict[str, Any], cell: Cell, h: str
) -> None:
    """Campioni ``APPLIED`` che non compilano: confronto con il marcato e indizi di troncamento."""
    base = replace(cell, attack_id=None, attack_params_hash=None)
    exe_ref = execution_ref(replace(cell, source="attacked"), h)
    marked_ref = execution_ref(replace(base, source="llm_watermarked"), h)
    if not (store.exists(exe_ref) and store.exists(marked_ref)):
        sys.stdout.write("  execution: MISSING\n")
        return
    exe = store.read_table(exe_ref).set_index("sample_id")
    status = {str(k): str(v) for k, v in exe["status"].items()}
    marked_exe = store.read_table(marked_ref)
    marked = {
        str(k): str(v) for k, v in zip(marked_exe["sample_id"], marked_exe["status"], strict=True)
    }
    applied = samples[samples["attack_status"] == "APPLIED"].set_index("sample_id")
    broken = [s for s in applied.index if status.get(s) in NOT_COMPILING]
    parents = [str(applied.at[s, "parent_id"]) for s in broken]
    was_ok = sum(marked.get(p) not in NOT_COMPILING for p in parents)
    wm = store.read_table(
        watermarked_ref(str(cell.method), str(cell.model_id), h, LEVEL, str(cell.language), SPLIT)
    )
    parent_code = {str(k): str(v) for k, v in zip(wm["sample_id"], wm["code"], strict=True)}
    fenced = sum(open_fence(raw.get(p)) for p in parents)
    ratios = [
        len(str(applied.at[s, "code"])) / max(1, len(parent_code.get(p, "")))
        for s, p in zip(broken, parents, strict=True)
    ]
    stderr = Counter(first_line(exe.at[s, "stderr_tail"]) for s in broken)
    sys.stdout.write(
        f"  APPLIED not compiling {len(broken)}/{len(applied)}: compiled when marked "
        f"{was_ok}, open fence {fenced}, length ratio attacked/marked median "
        f"{median(ratios) if ratios else 0:.2f}\n"
    )
    for line, n in stderr.most_common(5):
        sys.stdout.write(f"    {n:4d}  {line}\n")
    for s, p in list(zip(broken, parents, strict=True))[:2]:
        sys.stdout.write(f"    example {s} (parent {p})\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=["sweet", "stone", "promptmark", "mcgmark"])
    args = parser.parse_args()
    method = args.method
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, method)
    if record is None:
        sys.stdout.write(f"##### {method}: NOT FROZEN\n")
        return 1
    total_s = 0.0
    for attack in grid_attacks(ATTACK).values():
        for cell in record.cells:
            if cell.not_applicable or cell.config_hash is None:
                continue
            model, lang, h = cell.model_id, cell.language, cell.config_hash
            ph = attack.params_hash(lang)
            ref = attacked_ref(ATTACK, ph, method, model, h, LEVEL, lang, SPLIT)
            sys.stdout.write(f"\n##### {ATTACK} {method} {model} {lang}\n")
            if not store.exists(ref):
                sys.stdout.write("  attacked: MISSING\n")
                continue
            samples = store.read_table(ref)
            run_dir = store.root.joinpath(
                "_runs", "attack", ATTACK, ph, method, model, h, f"{LEVEL}_{lang}_{SPLIT}"
            )
            res = results(run_dir / "results.jsonl")
            raw = {str(r["item_id"]): r.get("raw_output") for r in res}
            secs = sum(float(r.get("elapsed_s") or 0.0) for r in res)
            total_s += secs
            worker_summary(raw, res, secs)
            tool_errors(samples)
            target = Cell(
                method=method, model_id=model, level=LEVEL, language=lang, split=SPLIT,
                attack_id=ATTACK, attack_params_hash=ph,
            )  # fmt: skip
            not_compiling(store, samples, raw, target, h)
    sys.stdout.write(f"\nTOTAL {method} T3.1 worker time {total_s / 3600:.2f} h\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
