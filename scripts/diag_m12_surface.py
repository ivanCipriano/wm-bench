"""Diagnostica delle differenze superficiali fra campioni marcati e riferimento (M12, ADR-015).

Serve a capire un AUROC avversario sospetto (es. UniXcoder = 1,000 su ACW): un classificatore
perfetto con CodeBLEU alto indica di solito un artefatto di superficie (spazi, a capo, virgolette,
intestazioni) introdotto dallo strumento d'inserimento.

Uso (con bench-core, nodo di login, nessun job; pochi secondi):
    python scripts/diag_m12_surface.py acw | tee docs/milestone_logs/M12_acw_surface.txt
    (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)

Per ogni cella congelata (L1 test) e riferimento stampa, per marcati e riferimento, la frazione di
campioni con ciascuna proprietà di superficie (a capo finale, CR, tab, spazi a fine riga, righe
vuote, virgolette, primo carattere, ...) e, se il metodo ha un campione d'origine (ACW:
``parent_id``), quanti marcati sono identici all'origine, quanti lo diventano normalizzando gli
spazi e tre esempi del primo punto in cui marcato e origine differiscono.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from statistics import median
from typing import Any

import pandas as pd

from bench.config.builder import load_experiment
from bench.hpo.freeze import load_freeze
from bench.metrics import imperceptibility as imp
from bench.pipeline.stage import Cell
from bench.pipeline.stages.imperceptibility import ImperceptibilityMetricsStage, references
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
N_EXAMPLES = 3
ASCII_MAX = 127
FLAG_GAP = 0.2  # differenza di frequenza da segnalare

PROPERTIES: dict[str, Callable[[str], bool]] = {
    "ends_with_newline": lambda c: c.endswith("\n"),
    "ends_with_2_newlines": lambda c: c.endswith("\n\n"),
    "starts_with_whitespace": lambda c: c[:1].isspace(),
    "has_cr": lambda c: "\r" in c,
    "has_tab": lambda c: "\t" in c,
    "has_trailing_spaces": lambda c: any(x != x.rstrip(" \t") for x in c.split("\n")),
    "has_blank_line": lambda c: "\n\n" in c,
    "has_double_blank_line": lambda c: "\n\n\n" in c,
    "has_single_quote": lambda c: "'" in c,
    "has_double_quote": lambda c: '"' in c,
    "has_triple_double_quote": lambda c: '"""' in c,
    "has_comment": lambda c: re.search(r"(^|\s)#", c) is not None,
    "has_import": lambda c: re.search(r"^\s*(import|from)\s", c, re.M) is not None,
    "indent_2_spaces": lambda c: re.search(r"^  \S", c, re.M) is not None,
    "has_non_ascii": lambda c: any(ord(ch) > ASCII_MAX for ch in c),
}


def squash(code: str) -> str:
    """Codice senza differenze di spazi: righe senza spazi finali, senza righe vuote."""
    return "\n".join(x.rstrip() for x in code.replace("\r", "").split("\n") if x.strip())


def first_diff(a: str, b: str) -> str:
    i = next((k for k, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    lo = max(0, i - 40)
    return (
        f"at char {i}/{len(a)}|{len(b)}\n"
        f"      parent: {a[lo : i + 60]!r}\n      marked: {b[lo : i + 60]!r}"
    )


def surface(marked: pd.DataFrame, reference: pd.DataFrame) -> None:
    sys.stdout.write(f"  {'property':<26} {'marked':>8} {'reference':>10}\n")
    for name, test in PROPERTIES.items():
        m = sum(test(str(c)) for c in marked["code"]) / len(marked)
        r = sum(test(str(c)) for c in reference["code"]) / len(reference)
        flag = "  <--" if abs(m - r) > FLAG_GAP else ""
        sys.stdout.write(f"  {name:<26} {m:>8.3f} {r:>10.3f}{flag}\n")
    for label, part in (("marked", marked), ("reference", reference)):
        codes = [str(c) for c in part["code"]]
        chars = median(len(c) for c in codes)
        lines = median(c.count("\n") + 1 for c in codes)
        sys.stdout.write(f"  {label}: chars median {chars:.0f}, lines median {lines:.0f}\n")


def exact(marked: pd.DataFrame, reference: pd.DataFrame) -> None:
    parents = reference.set_index("sample_id")["code"].astype(str)
    pairs = [(parents[p], str(c)) for p, c in zip(marked["parent_id"], marked["code"], strict=True)
             if p in parents.index]  # fmt: skip
    same = sum(a == b for a, b in pairs)
    same_squashed = sum(squash(a) == squash(b) for a, b in pairs)
    sys.stdout.write(
        f"  exact pairs {len(pairs)}: identical {same}, identical after whitespace "
        f"normalisation {same_squashed}\n"
    )
    for a, b in [p for p in pairs if p[0] != p[1]][:N_EXAMPLES]:
        sys.stdout.write(f"    first difference {first_diff(a, b)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=["sweet", "acw", "stone", "promptmark", "mcgmark"])
    args = parser.parse_args()
    method = args.method
    cfg = load_experiment(
        ["paths=cluster", "stage=metrics_imperceptibility", f"methods=[{method}]"]
    )
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, method)
    if record is None:
        sys.stdout.write(f"##### {method}: NOT FROZEN\n")
        return 1
    stage = ImperceptibilityMetricsStage(cfg)
    for frozen in record.cells:
        if frozen.not_applicable or frozen.config_hash is None:
            continue
        cell = Cell(
            method=method,
            model_id=frozen.model_id,
            level=LEVEL,
            language=frozen.language,
            split=SPLIT,
            config_hash=frozen.config_hash,
        )
        marked: Any = imp.non_empty(store.read_table(stage.samples(cell, None)))
        for r in references(method):
            reference: Any = imp.non_empty(store.read_table(stage.samples(cell, r)))
            sys.stdout.write(
                f"\n##### {method} {frozen.model_id} {frozen.language} vs {r} "
                f"(marked {len(marked)}, reference {len(reference)})\n"
            )
            surface(marked, reference)
            if "parent_id" in marked.columns and marked["parent_id"].notna().any():
                exact(marked, reference)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
