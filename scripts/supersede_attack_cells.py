"""Segna come superate le celle di un attacco calcolate con una versione precedente del catalogo
di un linguaggio (``catalog_version``; decisione dell'utente del 27 ottobre 2026, T2.1 C++).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/supersede_attack_cells.py METHOD ATTACK --language cpp --old-version 1 \\
        --reason "..." [--apply]

Per ogni punto della griglia e ogni cella congelata del metodo in ``--language`` calcola l'hash
della versione ``--old-version`` e quello corrente; gli artefatti della versione vecchia (attack,
execute, detect, metrics) **restano dove sono**, con dati e hash invariati: nel manifest si
aggiunge ``extra.superseded`` con l'hash corrente, la versione e il motivo. I report
(``check_attacks.py``) leggono solo le celle con l'hash corrente. Senza ``--apply`` stampa solo
cosa farebbe.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import UTC, datetime

from bench.attacks.base import attack_params_hash, grid_attacks
from bench.config.builder import load_experiment
from bench.hpo.freeze import FINAL_LEVELS, load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.metrics import metrics_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method")
    parser.add_argument("attack")
    parser.add_argument("--language", required=True)
    parser.add_argument("--old-version", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, args.method)
    if record is None:
        sys.stdout.write(f"##### {args.method}: NOT FROZEN\n")
        return 1
    now = datetime.now(UTC).isoformat()
    marked = missing = 0
    for attack in grid_attacks(args.attack).values():
        current = attack.catalog_version(args.language)
        if current == args.old_version:
            sys.stdout.write(f"##### {args.language}: catalog_version is still {current}\n")
            return 1
        old = attack_params_hash(attack.attack_id, attack.params, attack.version, args.old_version)
        new = attack.params_hash(args.language)
        info = {
            "by_attack_params_hash": new,
            "old_catalog_version": args.old_version,
            "catalog_version": current,
            "language": args.language,
            "reason": args.reason,
            "at": now,
        }
        for cell in record.cells:
            if cell.language != args.language or cell.not_applicable or cell.config_hash is None:
                continue
            model, h = cell.model_id, cell.config_hash
            base = Cell(
                method=args.method, model_id=model, level=LEVEL, language=args.language,
                split=SPLIT, attack_id=args.attack, attack_params_hash=old,
            )  # fmt: skip
            refs = [
                attacked_ref(args.attack, old, args.method, model, h, LEVEL, args.language, SPLIT),
                execution_ref(replace(base, source="attacked"), h),
                detection_ref(
                    args.method, model, h, attacked_subject(args.attack, old), LEVEL,
                    args.language, SPLIT,
                ),
                metrics_ref(
                    args.method, model, h, LEVEL, args.language, SPLIT, FINAL_LEVELS,
                    (args.attack, old),
                ),
            ]  # fmt: skip
            for ref in refs:
                if not store.exists(ref):
                    missing += 1
                    sys.stdout.write(f"  missing  {ref.path}\n")
                    continue
                marked += 1
                verb = "superseded" if args.apply else "would mark"
                sys.stdout.write(f"  {verb} {ref.path} (by {new})\n")
                if args.apply:
                    store.mark_superseded(ref, info)
    sys.stdout.write(
        f"##### {marked} artifacts {'marked' if args.apply else 'to mark'}, {missing} missing\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
