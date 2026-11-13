"""Esportazione degli ingressi dell'inventario dei siti (PIPELINE_metodo.md di multimark, P0).

Gira in **bench-core**. L'inventario (``scripts/inventory_sites.py``) gira invece nell'ambiente
``posthoc``, che non ha pandas né pyarrow e non sa comporre le canoniche come gli executor
(decisione dell'utente del 13 novembre 2026): questo script legge le tabelle del benchmark e
scrive un JSONL per gruppo, con il codice **esattamente** come lo usa il benchmark:

- canoniche: ``executor.canonical(problem)`` dell'executor del dataset (per EvalPlus il
  ``canonical_program`` della ground truth, per CodeNet e ClassEval la soluzione umana), come la
  fase ``execute`` con ``source=canonical``;
- baseline: ``code`` dei campioni di ``baseline/<modello>/<L>_python_dev.parquet``.

Gruppi (file ``<gruppo>.jsonl`` in ``--out-dir``):

| gruppo | contenuto |
|---|---|
| ``L1_dev_canonical`` | canoniche di L1 Python dev (HumanEval+ e MBPP+) |
| ``L1_dev_baseline_<modello>`` | baseline di L1 Python dev, per modello |
| ``L2_dev_canonical`` | canoniche di L2 Python dev (CodeNet) |
| ``L2_dev_baseline_<modello>`` | baseline di L2 Python dev, per modello |
| ``L3_classeval_canonical`` | canoniche di ClassEval (L3; tutte le classi, qualunque split) |

Ogni riga: ``sample_id``, ``code``, ``dataset``, ``problem_key``, ``language``, ``source``
(``canonical``/``baseline``), ``model_id`` e ``sample_index`` (``null`` per le canoniche),
``split``, ``loc`` (righe di codice di ``bench.lang.line_counter.count_code_lines``). Per le
canoniche ``sample_id`` è quello della ripetizione 0 della fase ``execute`` (unione con
``execution/canonical/...``); per la baseline è il ``sample_id`` del campione.

Determinismo: righe ordinate per (dataset, problem_key, sample_index), chiavi in ordine fisso,
UTF-8, fine riga ``\\n``. Un file che esiste già con lo stesso SHA-256 non si riscrive
(``[SKIP]``); con contenuto diverso lo script si ferma senza toccarlo (i risultati esistenti non si
sovrascrivono: spostarlo in ``superseded/`` e rilanciare). Lo stesso vale per il manifest di
ogni gruppo, ``<gruppo>.jsonl.manifest.json`` (tabella letta con lo SHA-256 del suo manifest
nello store, righe e SHA-256 del JSONL; senza date).

Uso (nodo di calcolo o di login, bench-core; vedi scripts/inventory_sites.sh)::

    python scripts/export_inventory_inputs.py [--out-dir DIR] [--models qwen25_coder_7b ...] \\
        [--groups L1_dev_canonical ...] [override Hydra ...]
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment
from bench.domain.enums import Language, Source
from bench.domain.ids import sample_id
from bench.domain.models import Problem
from bench.execution.executors import Executor
from bench.execution.sandbox import LocalSandbox
from bench.lang.line_counter import count_code_lines
from bench.pipeline.stage import Cell
from bench.pipeline.stages.execute import ExecuteStage
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.store.artifact_store import ArtifactStore
from bench.store.refs import ArtifactRef

LANGUAGE = "python"
ROW_FIELDS = (
    "sample_id",
    "code",
    "dataset",
    "problem_key",
    "language",
    "source",
    "model_id",
    "sample_index",
    "split",
    "loc",
)
MANIFEST_SUFFIX = ".manifest.json"  # <gruppo>.jsonl.manifest.json


class ExportError(RuntimeError):
    """File di uscita già presente con contenuto diverso (non si sovrascrive)."""


@dataclass(frozen=True)
class Group:
    """Gruppo dell'inventario: un file JSONL."""

    name: str
    level: str
    source: str  # "canonical" o "baseline"
    split: str | None  # None: tutti gli split (ClassEval)
    dataset: str | None = None  # None: tutti i dataset del livello
    model_id: str | None = None


def groups_for(models: Iterable[str]) -> list[Group]:
    """Gruppi da esportare, in ordine fisso."""
    models = list(models)
    out = [Group("L1_dev_canonical", "L1", "canonical", "dev")]
    out += [Group(f"L1_dev_baseline_{m}", "L1", "baseline", "dev", model_id=m) for m in models]
    out.append(Group("L2_dev_canonical", "L2", "canonical", "dev"))
    out += [Group(f"L2_dev_baseline_{m}", "L2", "baseline", "dev", model_id=m) for m in models]
    out.append(Group("L3_classeval_canonical", "L3", "canonical", None, dataset="classeval"))
    return out


def _sort_key(row: dict[str, Any]) -> tuple[str, str, int]:
    index = row["sample_index"]
    return str(row["dataset"]), str(row["problem_key"]), -1 if index is None else int(index)


def canonical_rows(
    problems: list[Problem], canonical_of: Callable[[Problem], str]
) -> list[dict[str, Any]]:
    """Righe delle canoniche (``canonical_of`` = ``executor.canonical`` del dataset)."""
    rows = []
    for p in problems:
        code = canonical_of(p)
        rows.append(
            {
                "sample_id": sample_id(
                    source=Source.HUMAN,
                    problem_key=p.problem_key,
                    language=str(p.language),
                    sample_index=0,
                ),
                "code": code,
                "dataset": p.dataset,
                "problem_key": p.problem_key,
                "language": str(p.language),
                "source": "canonical",
                "model_id": None,
                "sample_index": None,
                "split": None if p.split is None else str(p.split),
                "loc": count_code_lines(code, Language(str(p.language))),
            }
        )
    return sorted(rows, key=_sort_key)


def baseline_rows(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Righe della baseline (record di ``CodeSample``)."""
    rows = []
    for r in records:
        code = str(r["code"] or "")
        index = r["sample_index"]
        rows.append(
            {
                "sample_id": str(r["sample_id"]),
                "code": code,
                "dataset": str(r["dataset"]),
                "problem_key": str(r["problem_key"]),
                "language": str(r["language"]),
                "source": "baseline",
                "model_id": str(r["model_id"]),
                "sample_index": None if index is None else int(index),
                "split": None if r["split"] is None else str(r["split"]),
                "loc": count_code_lines(code, Language(str(r["language"]))),
            }
        )
    return sorted(rows, key=_sort_key)


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    """Contenuto JSONL deterministico (chiavi nell'ordine di ``ROW_FIELDS``)."""
    lines = [json.dumps({k: row[k] for k in ROW_FIELDS}, ensure_ascii=False) for row in rows]
    return "".join(line + "\n" for line in lines).encode("utf-8")


def write_if_new(path: Path, data: bytes) -> str:
    """Scrive ``data`` in modo atomico, salvo che il file esista già.

    Returns:
        ``"written"`` o ``"unchanged"`` (stesso SHA-256: nessuna scrittura).

    Raises:
        ExportError: se il file esiste con contenuto diverso.
    """
    digest = hashlib.sha256(data).hexdigest()
    if path.exists():
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if current == digest:
            return "unchanged"
        raise ExportError(
            f"{path} exists with different content (sha256 {current[:12]} != {digest[:12]}): "
            "move it to superseded/ and rerun"
        )
    ArtifactStore._check_write_static(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return "written"


class _Ctx:
    """Contesto minimo per ``ExecuteStage._executors`` (serve solo lo store)."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store


def _problems(store: ArtifactStore, group: Group) -> list[Problem]:
    table = store.read_table(problems_ref(group.level, LANGUAGE))
    if group.split is not None:
        table = table[table["split"] == group.split]
    if group.dataset is not None:
        table = table[table["dataset"] == group.dataset]
    problems = [Problem.model_validate(r) for r in table.to_dict(orient="records")]
    return sorted(problems, key=lambda p: p.problem_key)


def _canonical_code(executors: dict[str, Executor], problem: Problem) -> str:
    """Canonica come la compone l'executor del dataset (fase ``execute``)."""
    return executors[problem.dataset].canonical(problem)


def _input_sha(store: ArtifactStore, ref: ArtifactRef) -> str | None:
    manifest = store.read_manifest(ref)
    return manifest.data_sha256 if manifest else None


def export(
    cfg: Any,
    store: ArtifactStore,
    out_dir: Path,
    models: list[str],
    only: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Esporta i gruppi (tutti, o quelli di ``only``); restituisce i manifest per gruppo.

    Raises:
        ExportError: gruppo sconosciuto o file d'uscita già presente con contenuto diverso.
    """
    groups = groups_for(models)
    if only is not None:
        wanted = set(only)
        unknown = sorted(wanted - {g.name for g in groups})
        if unknown:
            raise ExportError(f"unknown groups {unknown}; known: {[g.name for g in groups]}")
        groups = [g for g in groups if g.name in wanted]
    stage = ExecuteStage(cfg)
    sandbox = LocalSandbox()  # le canoniche non eseguono nulla: nessun container
    manifests: dict[str, dict[str, Any]] = {}
    for group in groups:
        if group.source == "canonical":
            ref = problems_ref(group.level, LANGUAGE)
            problems = _problems(store, group)
            cell = Cell(source="canonical", level=group.level, language=LANGUAGE)
            executors: dict[str, Executor] = stage._executors(
                _Ctx(store),  # type: ignore[arg-type]
                cell,
                problems,
                sandbox,
            )
            rows = canonical_rows(problems, functools.partial(_canonical_code, executors))
        else:
            assert group.model_id is not None and group.split is not None
            ref = baseline_ref(group.model_id, group.level, LANGUAGE, group.split)
            records = store.read_table(ref).to_dict(orient="records")
            rows = baseline_rows({str(k): v for k, v in r.items()} for r in records)
        data = jsonl_bytes(rows)
        path = out_dir / f"{group.name}.jsonl"
        manifest = {
            "group": group.name,
            "input": {"path": ref.path.as_posix(), "sha256": _input_sha(store, ref)},
            "rows": len(rows),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        outcome = write_if_new(path, data)
        write_if_new(path.with_name(path.name + MANIFEST_SUFFIX), manifest_bytes)
        tag = "SKIP" if outcome == "unchanged" else "OK"
        print(f"[{tag}] {group.name}: {len(rows)} rows -> {path}", flush=True)
        manifests[group.name] = manifest
    return manifests


def default_out_dir(cfg: Any) -> Path:
    """Cartella di default: ``<artifacts>/_runs/diagnostics/inventory/inputs``."""
    return Path(cfg.paths.artifacts) / "_runs" / "diagnostics" / "inventory" / "inputs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out-dir", type=Path, help="default: <artifacts>/_runs/diagnostics/...")
    parser.add_argument("--models", nargs="+", help="default: models of configs/config.yaml")
    parser.add_argument("--groups", nargs="+", help="only these groups (default: all)")
    parser.add_argument("overrides", nargs="*", help="Hydra overrides (e.g. paths=local)")
    args = parser.parse_args(argv)
    cfg = load_experiment(args.overrides)
    store = ArtifactStore(Path(cfg.paths.artifacts))
    out_dir = args.out_dir or default_out_dir(cfg)
    try:
        export(cfg, store, out_dir, list(args.models or cfg.models), args.groups)
    except ExportError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
