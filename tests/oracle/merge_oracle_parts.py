"""Unisce le parti dell'oracle di MCGMark scritte da job paralleli (``oracle_method.sh``).

    python tests/oracle/merge_oracle_parts.py PARTS_DIR OUT_DIR

Legge ``PARTS_DIR/<nome>.<K>of<M>.json`` e scrive ``OUT_DIR/<nome>.json`` per ogni nome di
``NAMES`` presente: generazioni ordinate come negli input (``inputs.json``, o ``inputs_long.json``
per i nomi ``*_long``), rilevazioni concatenate per parte. Verifica che ci siano tutte le parti
e che versioni e vocabolario coincidano.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

NAMES = ("original", "patched", "nospeed_long", "patched_long")
PART = re.compile(r"^(" + "|".join(NAMES) + r")\.(\d+)of(\d+)\.json$")


def main(argv: list[str]) -> int:
    parts_dir, out_dir = Path(argv[1]), Path(argv[2])
    found: dict[str, dict[int, dict[str, Any]]] = {name: {} for name in NAMES}
    totals: set[int] = set()
    for path in sorted(parts_dir.iterdir()):
        match = PART.match(path.name)
        if match:
            variant, k, m = match.group(1), int(match.group(2)), int(match.group(3))
            found[variant][k] = json.loads(path.read_text(encoding="utf-8"))
            totals.add(m)
    if len(totals) != 1:
        raise SystemExit(f"parts with different totals in {parts_dir}: {sorted(totals)}")
    m = totals.pop()
    for variant, by_part in found.items():
        if not by_part:
            continue
        source = "inputs_long.json" if variant.endswith("_long") else "inputs.json"
        inputs = json.loads((out_dir / source).read_text(encoding="utf-8"))
        order = {p["problem_key"]: i for i, p in enumerate(inputs["prompts"])}
        missing = sorted(set(range(1, m + 1)) - set(by_part))
        if missing:
            raise SystemExit(f"{variant}: missing parts {missing} of {m} in {parts_dir}")
        first = by_part[1]
        for data in by_part.values():
            for field in ("variant", "transformers", "torch", "vocab_size"):
                if data[field] != first[field]:
                    raise SystemExit(f"{variant}: parts disagree on {field}")
        generations = [g for k in sorted(by_part) for g in by_part[k]["generations"]]
        generations.sort(key=lambda g: order[g["problem_key"]])
        if len(generations) != len(order):
            raise SystemExit(f"{variant}: {len(generations)} generations, expected {len(order)}")
        merged = {
            **{f: first[f] for f in ("variant", "transformers", "torch", "vocab_size")},
            "generations": generations,
            "detections": [d for k in sorted(by_part) for d in by_part[k]["detections"]],
        }
        out = out_dir / f"{variant}.json"
        out.write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        sys.stdout.write(f"wrote {out}: {m} parts, {len(generations)} generations\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
