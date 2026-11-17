"""Controlla che una fase abbia finito: ogni uscita di ogni cella pianificata esiste ed è completa.

Pianifica le celle come ``bench submit`` (``CellPlanner`` con gli stessi override Hydra) e, per
ogni cella, guarda le uscite della fase con il criterio della pipeline per saltare una cella
(``ArtifactStore.exists``: file, manifest ``complete``, stessa dimensione). Stampa le celle
mancanti (al più ``--show``) e un riepilogo; esce con 0 se non manca nulla, 1 altrimenti. Non
scrive niente e non lancia job: serve agli script a fasi (``scripts/run_p2_round2.sh``) per non
partire prima che la fase precedente sia finita.

Gira in **bench-core**, sul nodo di login::

    python scripts/check_stage_done.py stage=watermark methods=[mcss,multimark] levels=[L1,L2] \\
        splits=[dev] languages=[python] [hpo.grid=multimark] [--show 20]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--show", type=int, default=20, help="missing cells to print")
    p.add_argument("overrides", nargs="+", help="Hydra overrides, stage=<name> required")
    args = p.parse_args(argv)
    if not any(o.startswith("stage=") for o in args.overrides):
        print("[ERROR] stage=<name> is required", file=sys.stderr)
        return 2

    import bench.pipeline.stages  # noqa: F401  (popola STAGES)
    from bench.config.builder import load_experiment
    from bench.domain.errors import BenchError
    from bench.pipeline.stage import CellPlanner
    from bench.registry import STAGES
    from bench.store.artifact_store import ArtifactStore

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    try:
        cfg = load_experiment([*paths, *args.overrides])
        stage_cls = STAGES.get(cfg.stage)
        stage = stage_cls.create(cfg)
        store = ArtifactStore(cfg.paths.artifacts)
        cells = CellPlanner(cfg).cells_for(stage_cls)
        missing = []
        for cell in cells:
            refs = [str(r.path) for r in stage.outputs(cell) if not store.exists(r)]
            if refs:
                missing.append((cell, refs))
    except BenchError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    for cell, refs in missing[: args.show]:
        print(f"[MISSING] {cell.key()}: {', '.join(refs)}")
    if len(missing) > args.show:
        print(f"... and {len(missing) - args.show} more")
    done = len(cells) - len(missing)
    status = "DONE" if not missing else "NOT DONE"
    print(f"{cfg.stage}: {done}/{len(cells)} cells complete: {status}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
