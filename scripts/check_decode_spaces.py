"""Spazi spariti prima di ``!``, ``?``, ``,``, ``.`` nel codice generato: metodi contro baseline.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_decode_spaces.py [--artifacts DIR]

Richiesta dell'utente del 24 ottobre 2026. Con ``clean_up_tokenization_spaces`` attivo, ``decode``
sostituisce `` !`` con ``!`` (e lo stesso per ``?``, ``,``, ``.`` e `` 's``) in tutto il testo:
in un gruppo di campioni generati così quelle sequenze **non compaiono mai**, mentre nella baseline
(`` !=``, ``a ? b : c``) sono frequenti. Per ogni gruppo (fonte, metodo, modello, linguaggio) stampa
le occorrenze per campione nel testo (``raw_output``, o ``code`` se manca; i campioni vuoti, come
quelli ``NOT_APPLICABLE``, sono contati a parte) e segna ``SUSPECT`` i gruppi con
zero occorrenze di una sequenza che nella baseline dello stesso modello e linguaggio compare almeno
una volta ogni 20 campioni. Legge solo gli artefatti: baseline, baseline gemella e marcati.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
PATTERNS = (" !", " ?", " ,", " .", " 's")
MIN_BASELINE_RATE = 0.05  # almeno una occorrenza ogni 20 campioni
MIN_SAMPLES = 100


def _groups(root: Path) -> dict[tuple[str, str, str, str], list[Path]]:
    """File per (fonte, metodo, modello, linguaggio); il linguaggio viene dal nome del file."""
    groups: dict[tuple[str, str, str, str], list[Path]] = defaultdict(list)
    for path in sorted((root / "baseline").glob("*/*.parquet")):
        groups[("baseline", "-", path.parent.name, path.stem.split("_")[1])].append(path)
    for path in sorted((root / "baseline").glob("*/twin/*/*/*.parquet")):
        model, method = path.parents[3].name, path.parents[1].name
        groups[("twin", method, model, path.stem.split("_")[1])].append(path)
    for path in sorted((root / "watermarked").glob("*/*/*/*.parquet")):
        method, model = path.parents[2].name, path.parents[1].name
        groups[("marked", method, model, path.stem.split("_")[1])].append(path)
    return groups


def _rates(paths: list[Path]) -> tuple[dict[str, int], dict[str, float]]:
    """Occorrenze per campione sui testi non vuoti: ``raw_output`` se c'è, altrimenti ``code``
    (ACW non genera: il suo campione marcato ha solo ``code``)."""
    texts: list[str] = []
    counts = {"total": 0, "raw": 0, "code_only": 0, "empty": 0}
    for path in paths:
        table = pd.read_parquet(path)
        if "raw_output" in table.columns:
            raw = table["raw_output"]
        else:
            raw = pd.Series([None] * len(table))
        for r, c in zip(raw.fillna(""), table["code"].fillna(""), strict=True):
            counts["total"] += 1
            if str(r):
                counts["raw"] += 1
                texts.append(str(r))
            elif str(c):
                counts["code_only"] += 1
                texts.append(str(c))
            else:
                counts["empty"] += 1
    n = len(texts)
    return counts, {p: (sum(t.count(p) for t in texts) / n if n else 0.0) for p in PATTERNS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    args = parser.parse_args()
    groups = _groups(Path(args.artifacts))
    rates = {key: _rates(paths) for key, paths in groups.items()}
    header = f"{'source':<8} {'method':<11} {'model':<20} {'lang':<10} {'n':>6} {'raw':>6} "
    header += f"{'code':>6} {'empty':>6}  "
    sys.stdout.write(header + "  ".join(f"{p!r:>6}" for p in PATTERNS) + "  flag\n")
    suspects = 0
    for key in sorted(rates):
        source, method, model, lang = key
        counts, rate = rates[key]
        n = counts["raw"] + counts["code_only"]
        base = rates.get(("baseline", "-", model, lang), ({}, {}))[1]
        missing = [
            p
            for p in PATTERNS
            if source != "baseline"
            and n >= MIN_SAMPLES
            and rate[p] == 0
            and base.get(p, 0.0) >= MIN_BASELINE_RATE
        ]
        flag = f"SUSPECT (absent: {', '.join(repr(p) for p in missing)})" if missing else ""
        if n == 0:
            flag = "no text (all samples empty)"
        suspects += bool(missing)
        cells = "  ".join(f"{rate[p]:>6.3f}" for p in PATTERNS)
        sizes = " ".join(f"{counts[k]:>6}" for k in ("total", "raw", "code_only", "empty"))
        row = f"{source:<8} {method:<11} {model:<20} {lang:<10} {sizes}  {cells}  {flag}"
        sys.stdout.write(row + "\n")
    sys.stdout.write(f"##### groups {len(rates)}, suspect {suspects}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
