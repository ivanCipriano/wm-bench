"""Spazi spariti prima di ``!``, ``?``, ``,``, ``.`` nel codice generato: metodi contro baseline.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_decode_spaces.py [--artifacts DIR]

Richiesta dell'utente del 24 ottobre 2026. Con ``clean_up_tokenization_spaces`` attivo, ``decode``
sostituisce `` !`` con ``!`` (e lo stesso per ``?``, ``,``, ``.`` e `` 's``) in tutto il testo:
in un gruppo di campioni generati così quelle sequenze **non compaiono mai**, mentre nella baseline
(`` !=``, ``a ? b : c``) sono frequenti. Per ogni gruppo (fonte, metodo, modello, linguaggio) stampa
le occorrenze per campione nell'output grezzo (``raw_output``) e segna ``SUSPECT`` i gruppi con
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


def _rates(paths: list[Path]) -> tuple[int, dict[str, float]]:
    texts: list[str] = []
    for path in paths:
        try:
            column = pd.read_parquet(path, columns=["raw_output"])["raw_output"]
        except (KeyError, ValueError):
            column = pd.read_parquet(path, columns=["code"])["code"]
        texts += [str(t) for t in column.fillna("")]
    n = len(texts)
    return n, {p: (sum(t.count(p) for t in texts) / n if n else 0.0) for p in PATTERNS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    args = parser.parse_args()
    groups = _groups(Path(args.artifacts))
    rates = {key: _rates(paths) for key, paths in groups.items()}
    header = f"{'source':<8} {'method':<11} {'model':<20} {'lang':<10} {'n':>7}  "
    sys.stdout.write(header + "  ".join(f"{p!r:>6}" for p in PATTERNS) + "  flag\n")
    suspects = 0
    for key in sorted(rates):
        source, method, model, lang = key
        n, rate = rates[key]
        base = rates.get(("baseline", "-", model, lang), (0, {}))[1]
        missing = [
            p
            for p in PATTERNS
            if source != "baseline"
            and n >= MIN_SAMPLES
            and rate[p] == 0
            and base.get(p, 0.0) >= MIN_BASELINE_RATE
        ]
        flag = f"SUSPECT (absent: {', '.join(repr(p) for p in missing)})" if missing else ""
        suspects += bool(missing)
        cells = "  ".join(f"{rate[p]:>6.3f}" for p in PATTERNS)
        sys.stdout.write(
            f"{source:<8} {method:<11} {model:<20} {lang:<10} {n:>7}  {cells}  {flag}\n"
        )
    sys.stdout.write(f"##### groups {len(rates)}, suspect {suspects}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
