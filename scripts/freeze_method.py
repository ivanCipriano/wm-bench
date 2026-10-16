"""Congelamento della configurazione definitiva di un metodo (Milestone 10, ADR-013).

Uso (con bench-core, nodo di login, nessun job; pochi secondi):
    python scripts/freeze_method.py METHOD | tee docs/milestone_logs/M10_freeze_METHOD.txt

Controlla che la scelta e le soglie del metodo siano quelle definitive su L1+L2 e che la griglia
non sia cambiata; poi scrive ``freeze/<metodo>.json`` e stampa il record (data, commit,
configurazione e sha256 di selezione e soglia per cella). Se il congelamento non è possibile esce
con codice 1 ed elenca quello che manca. Lanciato di nuovo su un metodo già congelato stampa solo
il record esistente: un nuovo congelamento sarebbe una deviazione (ADR-013).
"""

from __future__ import annotations

import argparse
import sys

from bench.config.builder import load_experiment
from bench.domain.errors import BenchError
from bench.hpo.freeze import freeze_ref, load_freeze
from bench.pipeline.facade import BenchmarkFacade

METHODS = ("sweet", "acw", "stone", "promptmark", "mcgmark")
LANGUAGES = "[python,java,cpp,javascript]"  # i linguaggi non supportati: NOT_APPLICABLE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=METHODS)
    args = parser.parse_args()
    method = args.method
    cfg = load_experiment(
        ["paths=cluster", "stage=freeze", f"methods=[{method}]", f"languages={LANGUAGES}"]
    )
    facade = BenchmarkFacade(cfg)
    try:
        report = facade.run_stage("freeze")
    except BenchError as exc:
        sys.stdout.write(f"##### {method}: NOT FROZEN\n{exc}\n")
        return 1
    record = load_freeze(facade.store, method)
    manifest = facade.store.read_manifest(freeze_ref(method))
    if record is None or manifest is None:
        sys.stdout.write(f"##### {method}: no freeze record after the stage\n")
        return 1
    state = "frozen now" if report.ran else "already frozen (record unchanged)"
    prov = manifest.provenance
    sys.stdout.write(
        f"##### {method}: {state}\n"
        f"frozen_at   {record.frozen_at.isoformat()}\n"
        f"commit      {prov.repo_commit} (dirty: {prov.repo_dirty})\n"
        f"levels      {record.levels}\n"
        f"grid_sha256 {record.grid_sha256} ({len(record.candidate_hashes)} candidates)\n"
        f"record      {freeze_ref(method).path} (sha256 {manifest.data_sha256})\n"
    )
    for cell in record.cells:
        if cell.not_applicable:
            sys.stdout.write(f"  {cell.model_id:<20} {cell.language:<11} NOT_APPLICABLE\n")
            continue
        sys.stdout.write(
            f"  {cell.model_id:<20} {cell.language:<11} {cell.config_hash} "
            f"{cell.hparams}  τ {cell.threshold}  constraint {cell.constraint_satisfied}\n"
            f"      selection sha256 {cell.selected_sha256}\n"
            f"      threshold sha256 {cell.threshold_sha256}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
