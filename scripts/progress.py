"""Avanzamento dei worker in corso: campioni fatti e mancanti per esecuzione (``_runs``).

Uso (con bench-core, nodo di login, nessun job; legge soltanto):
    python scripts/progress.py                         # esecuzioni toccate nelle ultime 24 ore
    python scripts/progress.py --method acw --stage watermark   # con un filtro: di qualunque data
    python scripts/progress.py --method acw --stage watermark
    python scripts/progress.py --hours 0 --incomplete  # tutte quelle non finite, di qualunque data

Per ogni cartella ``_runs/<fase>/<metodo>/.../<livello>_<linguaggio>_<parte>`` con ``items.jsonl``:

- attesi: un risultato per item, o ``n`` per gli item che generano da un prompt (``embed`` con
  ``prompt_messages``);
- fatti: risultati distinti per ``(item_id, sample_index)`` in ``results.jsonl`` (le righe
  ripetute dopo una ripresa contano una volta);
- secondi per campione (mediana di ``elapsed_s``), stima del tempo che manca con un worker e ora
  dell'ultima scrittura (UTC): se è vecchia mentre il job risulta in esecuzione, il worker è fermo.

L'artefatto della fase si scrive solo alla fine: un'esecuzione al 100% non è ancora ``DONE`` finché
il job non ha finito.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment


def expected(items_path: Path) -> int:
    total = 0
    with items_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            total += int(item.get("n") or 1) if item.get("prompt_messages") else 1
    return total


def done(results_path: Path) -> tuple[int, float | None]:
    """Risultati distinti e mediana dei secondi per campione."""
    if not results_path.is_file():
        return 0, None
    last: dict[tuple[Any, Any], float] = {}
    with results_path.open(encoding="utf-8") as handle:
        for i, line in enumerate(handle):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:  # ultima riga a metà mentre il worker scrive
                continue
            last[(row.get("item_id", i), row.get("sample_index"))] = float(
                row.get("elapsed_s") or 0.0
            )
    return len(last), (statistics.median(last.values()) if last else None)


def _hms(seconds: float) -> str:
    h, rest = divmod(int(seconds), 3600)
    return f"{h}h{rest // 60:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", default="", help="es. watermark, detect, attack")
    parser.add_argument("--method", default="", help="es. acw, sweet")
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="solo le scritte nelle ultime N ore (0: tutte; default 24, 0 con --stage/--method)",
    )
    parser.add_argument("--incomplete", action="store_true", help="solo quelle non finite")
    args = parser.parse_args()
    hours = args.hours if args.hours is not None else (0 if args.stage or args.method else 24)
    cfg = load_experiment(["paths=cluster", "stage=selftest"])
    runs = Path(cfg.paths.artifacts) / "_runs"
    base = runs / args.stage if args.stage else runs
    now = time.time()
    rows = []
    for items in sorted(base.rglob("items.jsonl")):
        run = items.parent
        rel = run.relative_to(runs)
        if args.method and rel.parts[1:2] != (args.method,):  # _runs/<fase>/<metodo>/...
            continue
        results = run / "results.jsonl"
        stamp = (results if results.is_file() else items).stat().st_mtime
        if hours and now - stamp > hours * 3600:
            continue
        n_exp = expected(items)
        n_done, sec = done(results)
        if args.incomplete and n_done >= n_exp:
            continue
        eta = _hms((n_exp - n_done) * sec) if sec is not None and n_done < n_exp else "-"
        last = datetime.fromtimestamp(stamp, UTC).strftime("%m-%d %H:%M")
        rows.append((str(rel), n_done, n_exp, sec, eta, last))
    if not rows:
        sys.stdout.write("no run matched\n")
        return 0
    width = max(len(r[0]) for r in rows)
    sys.stdout.write(f"{'run':<{width}}  {'done':>6} / {'total':<6} {'%':>5}  {'s/sample':>8}"
                     f"  {'left (1 worker)':>15}  last write UTC\n")  # fmt: skip
    for rel, n_done, n_exp, sec, eta, last in rows:
        pct = 100 * n_done / n_exp if n_exp else 100.0
        s = f"{sec:.1f}" if sec is not None else "-"
        sys.stdout.write(f"{rel:<{width}}  {n_done:>6} / {n_exp:<6} {pct:>5.1f}  {s:>8}"
                         f"  {eta:>15}  {last}\n")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
