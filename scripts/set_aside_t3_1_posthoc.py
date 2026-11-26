"""Mette da parte gli artefatti T3.1 sul test di ``mcss`` e ``multimark`` calcolati prima della
correzione del 26 novembre 2026 (``RemarkAttack._post_hoc_multibit``; docs/deviations.md, P6).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/set_aside_t3_1_posthoc.py                           # stampa soltanto (v1)
    python scripts/set_aside_t3_1_posthoc.py --apply                   # rinomina (v1)
    python scripts/set_aside_t3_1_posthoc.py --stage detect [--apply]  # rilevazione della v2

Prima della correzione T3.1 trattava come errore lo stato ``PARTIAL`` della libreria (inserimento
riuscito con meno siti dei 24 bit) e quasi tutti i campioni finivano in ``TOOL_ERROR`` con il
codice marcato con k1. L'hash di T3.1 non cambia (``version`` e ``catalog_version`` restano 1: il
cambio varrebbe per tutti i metodi e ``configs/attack/t3_1_remark.yaml`` è congelato), quindi i
risultati vecchi si spostano accanto con un suffisso e le catene rifanno le celle mancanti.

``--stage all`` (default, v1): per ogni cella congelata (L1, python, test; per ``multimark`` le due
configurazioni) gli artefatti che dipendono dal codice attaccato: ``attacked``, ``execution`` degli
attaccati, ``detection`` con k1 e con k2 degli attaccati, metriche T3.1 e metriche della
ri-marcatura, con il suffisso ``.v1_partial_bug``.

``--stage detect`` (v2): la rilevazione della v2 aveva ripreso i punteggi della v1 dalla cartella
di lavoro del worker (``_runs/detect/...``), perché la ripresa riconosce gli item solo
dall'``item_id`` e gli id dei campioni attaccati non dipendono dal codice (docs/deviations.md, P6).
Con il suffisso ``.v2_stale_detect`` si spostano la ``detection`` con k1 e con k2 degli attaccati,
le metriche T3.1 e della ri-marcatura e le due cartelle di lavoro della rilevazione degli attaccati
(k1 e k2). Attacco ed esecuzione della v2 restano: l'attacco ha ripreso risultati del worker
calcolati sullo stesso input (codice marcato con k1, chiave k2, stesso seme), l'esecuzione è
ripartita da zero perché il suo file di ripresa controlla gli sha256 degli input.

Dati e manifest diventano ``<file><suffisso>`` e ``<manifest><suffisso>``, le cartelle
``<cartella><suffisso>``: nessun file si cancella, nessuno si sovrascrive (se una destinazione
esiste lo script si ferma prima di spostare). Restano sempre dove sono la rilevazione con k2 su dev
e sul test non attaccato, le soglie di k2 e il loro congelamento (non dipendono dal codice
attaccato) e la cartella di lavoro dell'attacco in ``_runs/attack/``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from bench.attacks.base import grid_attacks
from bench.config.builder import load_experiment
from bench.hpo.freeze import FINAL_LEVELS, load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.metrics import metrics_ref
from bench.pipeline.stages.remark import remark_metrics_ref
from bench.store.artifact_store import ArtifactStore
from bench.store.refs import ArtifactRef

METHODS = ("mcss", "multimark")
ATTACK, LEVEL, LANGUAGE, SPLIT = "T3.1", "L1", "python", "test"
SUFFIX = ".v1_partial_bug"
SUFFIXES = {"all": SUFFIX, "detect": ".v2_stale_detect"}
# Primo artefatto di ``t3_1_refs`` (in ordine) per fase: tutti, o dalla rilevazione in poi.
FIRST = {"all": 0, "detect": 2}


def frozen_cells(store: ArtifactStore, method: str) -> list[tuple[str, str, str]]:
    """(nome, modello, config_hash) delle celle congelate di L1 python."""
    record = load_freeze(store, method)
    if record is None:
        raise SystemExit(f"##### {method}: NOT FROZEN")
    out = []
    for cell in record.cells:
        if cell.language != LANGUAGE or cell.not_applicable or cell.config_hash is None:
            continue
        name = f"{method} {cell.label or ''} {cell.model_id} {cell.config_hash}"
        out.append((name.replace("  ", " "), cell.model_id, cell.config_hash))
    return out


def t3_1_refs(
    store: ArtifactStore, method: str, stage: str = "all"
) -> list[tuple[str, ArtifactRef]]:
    """(nome della cella, riferimento) degli artefatti T3.1 sul test delle celle congelate
    (``stage="detect"``: dalla rilevazione in poi)."""
    ((params_hash, _),) = grid_attacks(ATTACK).items()
    subject = attacked_subject(ATTACK, params_hash)
    attack = (ATTACK, params_hash)
    out = []
    for name, model, h in frozen_cells(store, method):
        base = Cell(
            method=method, model_id=model, level=LEVEL, language=LANGUAGE, split=SPLIT,
            attack_id=ATTACK, attack_params_hash=params_hash,
        )  # fmt: skip
        refs = [
            attacked_ref(ATTACK, params_hash, method, model, h, LEVEL, LANGUAGE, SPLIT),
            execution_ref(replace(base, source="attacked"), h),
            detection_ref(method, model, h, subject, LEVEL, LANGUAGE, SPLIT),
            detection_ref(method, model, h, subject, LEVEL, LANGUAGE, SPLIT, "k2"),
            metrics_ref(method, model, h, LEVEL, LANGUAGE, SPLIT, FINAL_LEVELS, attack),
            remark_metrics_ref(
                method, model, h, LEVEL, LANGUAGE, SPLIT, list(FINAL_LEVELS), attack
            ),  # fmt: skip
        ]
        out += [(name, ref) for ref in refs[FIRST[stage] :]]
    return out


def detect_run_dirs(store: ArtifactStore, method: str) -> list[tuple[str, Path]]:
    """Cartelle di lavoro del worker della rilevazione degli attaccati, con k1 e con k2 (come in
    ``DetectStage.run``: ``_runs/detect/<metodo>/<modello>/<config>/[k2/]<soggetto>/<cella>``)."""
    ((params_hash, _),) = grid_attacks(ATTACK).items()
    subject = attacked_subject(ATTACK, params_hash)
    out = []
    for name, model, h in frozen_cells(store, method):
        for key in ((), ("k2",)):
            base = store.root.joinpath("_runs", "detect", method, model, h, *key)
            out.append((name, base / subject / f"{LEVEL}_{LANGUAGE}_{SPLIT}"))
    return out


def moves(store: ArtifactStore, ref: ArtifactRef, suffix: str = SUFFIX) -> list[tuple[Path, Path]]:
    """Coppie (sorgente, destinazione) di dati e manifest che esistono."""
    files = (store.path_of(ref), store.manifest_of(ref))
    return [(src, src.with_name(src.name + suffix)) for src in files if src.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="rename (default: print only)")
    parser.add_argument("--methods", nargs="+", default=list(METHODS), choices=METHODS)
    parser.add_argument(
        "--stage", choices=list(SUFFIXES), default="all",
        help="all: v1, from the attack on; detect: v2 detection, metrics and detect work dirs",
    )  # fmt: skip
    args = parser.parse_args()
    suffix = SUFFIXES[args.stage]
    verb = "move" if args.apply else "would move"
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(Path(cfg.paths.artifacts))
    plan: list[tuple[Path, Path]] = []
    missing = 0
    for method in args.methods:
        for name, ref in t3_1_refs(store, method, args.stage):
            pairs = moves(store, ref, suffix)
            if not pairs:
                missing += 1
                sys.stdout.write(f"  missing  {name}  {ref.path}\n")
            for src, dst in pairs:
                sys.stdout.write(f"  {verb}  {src} -> {dst.name}\n")
            plan += pairs
        if args.stage == "detect":
            for name, run_dir in detect_run_dirs(store, method):
                if not run_dir.is_dir():
                    missing += 1
                    sys.stdout.write(f"  missing  {name}  {run_dir}/\n")
                    continue
                dst = run_dir.with_name(run_dir.name + suffix)
                sys.stdout.write(f"  {verb}  {run_dir}/ -> {dst.name}/\n")
                plan.append((run_dir, dst))
    clash = [dst for _, dst in plan if dst.exists()]
    if clash:
        sys.stdout.write("".join(f"  EXISTS   {p}\n" for p in clash))
        sys.stdout.write(f"##### {len(clash)} destinations exist: nothing moved\n")
        return 1
    if args.apply:
        for src, dst in plan:
            src.rename(dst)
    done = "moved" if args.apply else "to move (dry run, use --apply)"
    sys.stdout.write(f"##### {len(plan)} paths {done}, {missing} missing ({args.stage})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
