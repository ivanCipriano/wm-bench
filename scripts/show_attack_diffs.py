"""Diff fra marcati e attaccati di un metodo congelato su L1 test, con le modifiche classificate.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/show_attack_diffs.py METHOD ATTACK --params variant=conservative \\
        --language cpp [--n 5] [--regressions]

Per ogni modello conta i campioni ``APPLIED`` per tipo di modifica (decisione dell'utente del 26
ottobre 2026, prima di aggiungere a D33 il fenomeno di STONE C++ in T2.2(a)):

- ``preamble_only``: cambiano solo righe di contorno (``#include``, ``using``, ``import``,
  ``package``), righe vuote o commenti;
- ``preamble_signature``: come sopra più l'intestazione della funzione del problema;
- ``cosmetic_body``: nel corpo cambiano solo spazi o la qualificazione ``std::`` (le righe tolte e
  aggiunte coincidono dopo la normalizzazione);
- ``body``: altre modifiche al corpo.

Poi stampa ``--n`` diff completi, scelti in ordine ``derive_seed(global_seed, "attack-diffs",
sample_id)`` fra tutti i campioni ``APPLIED`` (non selezionati a mano).

Con ``--regressions`` conta e mostra solo i campioni ``APPLIED`` il cui esito d'esecuzione cambia
rispetto al marcato (es. ``PASSED`` → ``COMPILE_ERROR``), con il dettaglio dell'attacco (tipi
applicati) e la coda dello stderr: serve per T2.1, che deve preservare la semantica.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from dataclasses import replace
from typing import Any

from bench.attacks.base import grid_attacks
from bench.config.builder import load_experiment
from bench.domain.ids import derive_seed
from bench.hpo.freeze import load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import problems_ref
from bench.pipeline.stages.watermark import watermarked_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
PREAMBLE = ("#include", "using ", "import ", "package ", "#pragma")
COMMENT = ("//", "/*", "*", "#")
CATEGORIES = ("preamble_only", "preamble_signature", "cosmetic_body", "body")


def _kind(line: str, entry: str) -> str:
    """Tipo di una riga cambiata: preamble, comment (anche vuota), signature o body."""
    text = line.strip()
    if text.startswith(PREAMBLE):
        return "preamble"
    if not text or text.startswith(COMMENT):
        return "comment"
    if re.search(rf"\b{re.escape(entry)}\s*\(", text) and text.rstrip().endswith(("{", ")")):
        return "signature"
    return "body"


def _norm(line: str) -> str:
    return re.sub(r"\s+", "", line.replace("std::", ""))


def classify(original: str, attacked: str, entry: str) -> str:
    """Categoria della modifica di un campione (vedi la docstring del modulo)."""
    removed: list[str] = []
    added: list[str] = []
    kinds: set[str] = set()
    for line in difflib.ndiff(original.splitlines(), attacked.splitlines()):
        if line[:2] not in ("- ", "+ "):
            continue
        kind = _kind(line[2:], entry)
        kinds.add(kind)
        if kind == "body":
            (removed if line[0] == "-" else added).append(_norm(line[2:]))
    if "body" not in kinds:
        return "preamble_signature" if "signature" in kinds else "preamble_only"
    if Counter(removed) == Counter(added):
        return "cosmetic_body"
    return "body"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method")
    parser.add_argument("attack")
    parser.add_argument("--params", default="", help="filtro sui parametri: chiave=valore[,...]")
    parser.add_argument("--language", required=True)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument(
        "--regressions", action="store_true", help="solo i campioni con esito d'esecuzione cambiato"
    )
    args = parser.parse_args()
    wanted = dict(kv.split("=", 1) for kv in args.params.split(",") if kv)
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, args.method)
    if record is None:
        sys.stdout.write(f"##### {args.method}: NOT FROZEN\n")
        return 1
    attacks = {
        h: a
        for h, a in grid_attacks(args.attack).items()
        if all(str(a.params.get(k)) == v for k, v in wanted.items())
    }
    if len(attacks) != 1:
        sys.stdout.write(f"##### the filter {wanted} selects {len(attacks)} grid points\n")
        return 1
    ((params_hash, attack),) = attacks.items()
    problems = store.read_table(problems_ref(LEVEL, args.language))
    entries = dict(zip(problems["problem_key"], problems["entry_point"], strict=True))
    sys.stdout.write(
        f"##### {args.method} {args.attack} {attack.params} {args.language} {LEVEL} {SPLIT}\n"
    )
    applied: list[dict[str, Any]] = []
    for cell in record.cells:
        if cell.language != args.language or cell.not_applicable or cell.config_hash is None:
            continue
        ref = attacked_ref(
            args.attack, params_hash, args.method, cell.model_id, cell.config_hash, LEVEL,
            args.language, SPLIT,
        )  # fmt: skip
        table = store.read_table(ref)
        records = table[table["attack_status"] == "APPLIED"].to_dict(orient="records")
        rows = [{str(k): v for k, v in r.items()} for r in records]
        parents = store.read_table(
            watermarked_ref(
                args.method, cell.model_id, cell.config_hash, LEVEL, args.language, SPLIT
            )
        )
        marked = dict(zip(parents["sample_id"], parents["code"], strict=True))
        if args.regressions:
            rows = _regressions(store, args.method, args.attack, params_hash, cell, rows)
        counts: Counter[str] = Counter()
        for row in rows:
            original = marked[row["parent_id"]]
            entry = str(entries.get(row["problem_key"]) or "")
            category = classify(original, row["code"], entry)
            counts[category] += 1
            applied.append(
                {**{str(k): v for k, v in row.items()}, "original": original, "category": category}
            )
        n = len(table)
        selected = "APPLIED con esito cambiato" if args.regressions else "APPLIED"
        sys.stdout.write(f"\n=== {cell.model_id}: {n} campioni, {selected} {len(rows)}\n")
        for category in CATEGORIES:
            k = counts[category]
            share = k / len(rows) if rows else 0.0
            sys.stdout.write(f"  {category:<20} {k:>5}  ({share:.1%} degli APPLIED)\n")
    applied.sort(key=lambda r: derive_seed(cfg.global_seed, "attack-diffs", r["sample_id"]))
    for shown in applied[: args.n]:
        sys.stdout.write(
            f"\n----- {shown['model_id']} {shown['problem_key']} #{shown['sample_index']} "
            f"({shown['category']})\n"
        )
        if args.regressions:
            sys.stdout.write(
                f"exec {shown['exec_marked']} -> {shown['exec_attacked']}\n"
                f"attack_detail {shown['attack_detail']}\n"
                f"stderr_tail {shown['stderr_tail']}\n"
            )
        diff = difflib.unified_diff(
            shown["original"].splitlines(), shown["code"].splitlines(), "marcato", "attaccato",
            lineterm="",
        )  # fmt: skip
        sys.stdout.write("\n".join(diff) + "\n")
    return 0


def _regressions(
    store: ArtifactStore,
    method: str,
    attack_id: str,
    params_hash: str,
    cell: Any,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Campioni ``APPLIED`` con esito d'esecuzione diverso da quello del marcato (padre)."""
    base = Cell(
        method=method, model_id=cell.model_id, level=LEVEL, language=cell.language, split=SPLIT,
        attack_id=attack_id, attack_params_hash=params_hash,
    )  # fmt: skip
    attacked = store.read_table(execution_ref(replace(base, source="attacked"), cell.config_hash))
    marked = store.read_table(
        execution_ref(replace(base, source="llm_watermarked"), cell.config_hash)
    )
    before = dict(zip(marked["sample_id"], marked["status"], strict=True))
    after = attacked.set_index("sample_id")
    out = []
    for row in rows:
        old, new = str(before.get(row["parent_id"])), str(after.at[row["sample_id"], "status"])
        if old != new:
            detail = row.get("attack_detail")
            out.append(
                {
                    **row,
                    "exec_marked": old,
                    "exec_attacked": new,
                    "attack_detail": json.loads(detail) if isinstance(detail, str) else detail,
                    "stderr_tail": after.at[row["sample_id"], "stderr_tail"],
                }
            )
    return out


if __name__ == "__main__":
    raise SystemExit(main())
