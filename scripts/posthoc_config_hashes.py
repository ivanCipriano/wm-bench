"""``config_hash`` delle configurazioni di ``mcss`` e ``multimark`` con la libreria corrente.

Il ``config_hash`` è lo SHA-256 troncato di iperparametri nativi, versione del contratto e commit
del submodule della copia patchata (``build/patched/multimark.source_commit``, ADR-003): con la
libreria nuova ogni cella del secondo giro di P2 ha un hash diverso da quello del primo giro, e
quindi percorsi diversi in ``watermarked/``, ``execution/``, ``detection/``, ``thresholds/`` e
``metrics*/``. Lo script stampa, per ogni metodo, il default (``default``) e i punti della griglia
``configs/hpo/<metodo>.yaml`` (``grid:<iperparametri>``), una riga per configurazione::

    <metodo> <config_hash> <etichetta>

Con ``--only default`` o ``--only grid`` stampa solo l'hash (una riga per configurazione), per gli
script: ``scripts/run_p2_round2.sh`` lo passa a ``report_metrics.py --config`` e
``report_capacity.py --config-hash``. ``--expect-commit`` ferma lo script se la copia patchata è a
un altro commit (copia vecchia: gli hash sarebbero quelli del primo giro).

Gira in **bench-core**, legge solo la configurazione::

    python scripts/posthoc_config_hashes.py [--methods mcss multimark] [--only default|grid]
        [--expect-commit SHA] [override Hydra ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

METHODS = ("mcss", "multimark")


def rows(cfg: object, method: str) -> list[tuple[str, str, str]]:
    """(metodo, hash, etichetta) del default e dei punti della griglia, senza hash ripetuti."""
    from bench.hpo.grid import grid_path, load_grid
    from bench.pipeline.stages.watermark import make_adapter

    adapter = make_adapter(cfg, method)  # type: ignore[arg-type]
    out = [(method, adapter.config_hash(adapter.default_hparams()), "default")]
    if grid_path(method).is_file():
        for point in load_grid(method).points:
            h = adapter.config_hash(point.hparams)
            if all(h != seen for _, seen, _ in out):
                label = json.dumps(point.hparams, sort_keys=True, separators=(",", ":"))
                out.append((method, h, f"grid:{label}"))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--methods", nargs="+", default=list(METHODS), choices=METHODS)
    p.add_argument("--only", choices=("default", "grid"), help="print only these hashes")
    p.add_argument("--expect-commit", help="stop if the patched copy is at another commit")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)

    from bench.config.builder import load_experiment
    from bench.domain.errors import BenchError
    from bench.pipeline.stages.watermark import make_adapter

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    try:
        cfg = load_experiment([*paths, "stage=watermark", *args.overrides])
        for method in args.methods:
            commit = make_adapter(cfg, method).source_commit()
            if args.expect_commit and commit != args.expect_commit:
                print(
                    f"[ERROR] {method}: patched copy at {commit}, expected {args.expect_commit}: "
                    "run git submodule update and bash scripts/apply_patches.sh multimark",
                    file=sys.stderr,
                )
                return 1
        found = [r for m in args.methods for r in rows(cfg, m)]
    except BenchError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    for method, h, label in found:
        if args.only is None:
            print(f"{method} {h} {label}")
        elif (label == "default") == (args.only == "default"):
            print(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
