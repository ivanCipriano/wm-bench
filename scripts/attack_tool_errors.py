"""Errori più frequenti dei tool di un attacco su L1 test (diagnostica della Milestone 10).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/attack_tool_errors.py METHOD ATTACK LANGUAGE [--top 8]

Legge gli artefatti ``attacked/<ATTACK>/*/<METHOD>/*/*/L1_<LANGUAGE>_test.parquet`` e per ogni
cella (configurazione dell'attacco, modello) conta i ``TOOL_ERROR`` raggruppati per la prima riga
significativa dell'errore. Per ogni gruppo stampa anche quanti campioni marcati erano già
``PASSED`` all'esecuzione senza attacco: se sono pochi, il tool fallisce su codice già rotto.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from bench.config.builder import load_experiment

NOISE = re.compile(r"^(exit \d+: )?(\d+ warnings?|Suppressed|Use -header-filter|Error while)")
# Messaggi veri dei compilatori: clang (``error: ...``) e javac via Maven (``.java:[r,c] ...``).
MESSAGE = re.compile(r"(?:\berror: |\.java:\[\d+,\d+\] )(.+)")


def _key(error: str) -> str:
    """Primo messaggio d'errore del compilatore se c'è, altrimenti la prima riga utile; senza
    percorsi temporanei e numeri di riga."""
    found = MESSAGE.search(error)
    if found:
        return found.group(1).strip()[:160]
    for raw in error.splitlines():
        line = raw.strip()
        if not line or NOISE.match(line):
            continue
        return re.sub(r":\d+:\d+:", ":", re.sub(r"(?<!\S)/\S*/", "", line))[:160]
    return error[:160]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method")
    parser.add_argument("attack")
    parser.add_argument("language")
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    root = cfg.paths.artifacts
    files = sorted(
        root.glob(f"attacked/{args.attack}/*/{args.method}/*/*/L1_{args.language}_test.parquet")
    )
    if not files:
        sys.stdout.write("no attacked artifacts found\n")
        return 1
    for path in files:
        # attacked/<attacco>/<hash>/<metodo>/<modello>/<cfg>/<file>
        params_hash, model = path.parts[-5], path.parts[-3]
        df = pd.read_parquet(path)
        errors = df[df["attack_status"] == "TOOL_ERROR"]
        sys.stdout.write(
            f"##### {args.attack} {params_hash} {model} {args.language}: "
            f"{len(errors)}/{len(df)} TOOL_ERROR\n"
        )
        passed = _passed(root, path)
        groups: Counter[str] = Counter()
        passed_in: Counter[str] = Counter()
        for _, row in errors.iterrows():
            key = _key(str(json.loads(row["attack_detail"]).get("error", "")))
            groups[key] += 1
            passed_in[key] += int(row["parent_id"] in passed)
        for key, n in groups.most_common(args.top):
            sys.stdout.write(f"  {n:5d}  (marcati PASSED: {passed_in[key]:4d})  {key}\n")
    return 0


def _passed(root: Path, path: Path) -> set[str]:
    """``sample_id`` dei marcati che passano i test senza attacco (esecuzione della fase 0)."""
    method, model, cfg_hash = path.parts[-4], path.parts[-3], path.parts[-2]
    marked = root / "execution" / "watermarked" / method / model / cfg_hash / path.name
    if not marked.is_file():
        raise FileNotFoundError(f"marked execution not found: {marked}")
    df = pd.read_parquet(marked, columns=["sample_id", "status"])
    return set(df.loc[df["status"] == "PASSED", "sample_id"])


if __name__ == "__main__":
    raise SystemExit(main())
