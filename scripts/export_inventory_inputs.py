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
| ``L2_codenet_pool`` | solo con ``--groups``: sottomissioni umane CodeNet Python estratte in M9, |
| | tranne i negativi di test (per la selezione di verifica di ``mcss``, PIPELINE §4.4) |

Il pool (decisione dell'utente del 13 novembre 2026) prende tutti i file Python estratti sotto
``datasets.codenet.dirs.data`` (problemi candidati della M9, circa 10 sottomissioni ciascuno) ed
esclude i negativi di test: per ruolo nella selezione (``codenet_selected.yaml`` con lo split del
problema) e per codice uguale a un negativo di test di ``data/negatives/*.parquet``. Ogni riga ha
in più ``role`` (``canonical_dev``, ``canonical_test``, ``negative_dev``, ``unselected``); il
manifest del gruppo riporta i conteggi delle esclusioni.

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

import yaml

from bench.config.builder import load_experiment
from bench.data.codenet import LANG_NAMES
from bench.data.loaders.base import normalize_newlines
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
OPTIONAL_FIELDS = ("role",)  # solo nel pool CodeNet
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


POOL_GROUP = Group("L2_codenet_pool", "L2", "codenet_pool", None, dataset="codenet")
POOL_EXCLUDED_ROLES = frozenset({"negative_test"})


def groups_for(models: Iterable[str]) -> list[Group]:
    """Gruppi di default, in ordine fisso (il pool CodeNet si chiede con ``--groups``)."""
    models = list(models)
    out = [Group("L1_dev_canonical", "L1", "canonical", "dev")]
    out += [Group(f"L1_dev_baseline_{m}", "L1", "baseline", "dev", model_id=m) for m in models]
    out.append(Group("L2_dev_canonical", "L2", "canonical", "dev"))
    out += [Group(f"L2_dev_baseline_{m}", "L2", "baseline", "dev", model_id=m) for m in models]
    out.append(Group("L3_classeval_canonical", "L3", "canonical", None, dataset="classeval"))
    return out


def _sort_key(row: dict[str, Any]) -> tuple[str, str, int, str]:
    # ``sample_id`` solo come ultimo criterio: decide fra le sottomissioni dello stesso problema
    # del pool CodeNet e non cambia l'ordine degli altri gruppi (problem_key e indice unici).
    index = row["sample_index"]
    return (
        str(row["dataset"]),
        str(row["problem_key"]),
        -1 if index is None else int(index),
        str(row["sample_id"]),
    )


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


def submission_roles(
    selection: dict[str, Any], splits: dict[str, str | None]
) -> dict[tuple[str, str], str]:
    """Ruolo delle sottomissioni Python della selezione L2 (``codenet_selected.yaml``).

    ``(pid, submission_id)`` → ``canonical_<split>`` o ``negative_<split>``; le sottomissioni
    estratte che non compaiono qui sono ``unselected`` (problemi candidati non scelti o
    sottomissioni di riserva per la deduplicazione).
    """
    roles: dict[tuple[str, str], str] = {}
    for entry in selection.get("problems", []):
        key = str(entry["problem_key"])
        pid = key.split("/", 1)[1]
        split = splits.get(key) or "none"
        subs = entry["submissions"]["python"]
        roles[(pid, str(subs["canonical"]["submission_id"]))] = f"canonical_{split}"
        for sub in subs["negatives"]:
            roles[(pid, str(sub["submission_id"]))] = f"negative_{split}"
    return roles


def pool_rows(
    files: Iterable[Path],
    roles: dict[tuple[str, str], str],
    splits: dict[str, str | None],
    test_negative_codes: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Righe del pool CodeNet Python: sottomissioni umane estratte, tranne i negativi di test.

    ``files`` sono ``<data>/<pid>/Python/<submission_id>.<ext>``. Un file si esclude se è un
    negativo di test della selezione (ruolo ``negative_test``) **o** se il suo codice coincide
    con quello di un negativo di test di qualsiasi tabella (stessa lettura del loader, fine riga
    normalizzati): così restano fuori anche i duplicati esatti. Il codice si legge come
    ``CodeNetLoader._code``.

    Returns:
        righe ordinate e conteggi (``files``, ``excluded_role``, ``excluded_code``, ``kept`` e
        ``role=<ruolo>`` per le righe tenute).
    """
    rows: list[dict[str, Any]] = []
    stats: dict[str, int] = {"files": 0, "excluded_role": 0, "excluded_code": 0}
    for path in files:
        stats["files"] += 1
        pid, sub_id = path.parent.parent.name, path.stem
        role = roles.get((pid, sub_id), "unselected")
        code = normalize_newlines(path.read_text(encoding="utf-8", errors="replace"))
        if role in POOL_EXCLUDED_ROLES:
            stats["excluded_role"] += 1
            continue
        if code in test_negative_codes:
            stats["excluded_code"] += 1
            continue
        key = f"codenet/{pid}"
        rows.append(
            {
                "sample_id": f"{key}/python/{sub_id}",
                "code": code,
                "dataset": "codenet",
                "problem_key": key,
                "language": LANGUAGE,
                "source": POOL_GROUP.source,
                "model_id": None,
                "sample_index": None,
                "split": splits.get(key),
                "loc": count_code_lines(code, Language.PYTHON),
                "role": role,
            }
        )
        stats[f"role={role}"] = stats.get(f"role={role}", 0) + 1
    stats["kept"] = len(rows)
    return sorted(rows, key=_sort_key), dict(sorted(stats.items()))


def _pool(
    cfg: Any, store: ArtifactStore
) -> tuple[list[dict[str, Any]], dict[str, Any], ArtifactRef]:
    """Pool CodeNet dal disco, con ruoli, split e negativi di test letti dagli artefatti."""
    spec = cfg.datasets["codenet"]
    selection = yaml.safe_load(spec.file("selected").read_text(encoding="utf-8")) or {}
    ref = problems_ref("L2", LANGUAGE)
    problems = store.read_table(ref)
    splits = {
        str(k): (None if v is None else str(v))
        for k, v in zip(problems["problem_key"], problems["split"], strict=True)
    }
    test_codes: set[str] = set()
    tables: dict[str, str | None] = {}
    for path in sorted((store.root / "data" / "negatives").glob("*.parquet")):
        nref = ArtifactRef.of("negatives", f"data/negatives/{path.name}")
        if not store.exists(nref):
            continue
        table = store.read_table(nref)
        test = table[(table["split"] == "test") & (table["language"] == LANGUAGE)]
        test_codes.update(str(c) for c in test["code"])
        tables[nref.path.as_posix()] = _input_sha(store, nref)
    files = sorted(Path(spec.dirs["data"]).glob(f"*/{LANG_NAMES[Language.PYTHON]}/*"))
    rows, stats = pool_rows(
        (f for f in files if f.is_file()), submission_roles(selection, splits), splits, test_codes
    )
    extra = {
        "stats": stats,
        "negative_tables": tables,
        "test_negative_codes": len(test_codes),
        "global_seed": int(cfg.global_seed),  # per l'ordine della selezione di verifica di mcss
    }
    return rows, extra, ref


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    """Contenuto JSONL deterministico (chiavi nell'ordine di ``ROW_FIELDS``, poi i campi
    facoltativi di ``OPTIONAL_FIELDS`` se la riga li ha, come ``role`` del pool CodeNet)."""
    lines = [
        json.dumps(
            {k: row[k] for k in (*ROW_FIELDS, *(f for f in OPTIONAL_FIELDS if f in row))},
            ensure_ascii=False,
        )
        for row in rows
    ]
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
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")  # un nome per processo
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
        known = [*groups, POOL_GROUP]
        unknown = sorted(wanted - {g.name for g in known})
        if unknown:
            raise ExportError(f"unknown groups {unknown}; known: {[g.name for g in known]}")
        groups = [g for g in known if g.name in wanted]
    stage = ExecuteStage(cfg)
    sandbox = LocalSandbox()  # le canoniche non eseguono nulla: nessun container
    manifests: dict[str, dict[str, Any]] = {}
    for group in groups:
        pool_extra: dict[str, Any] | None = None
        if group is POOL_GROUP:
            rows, pool_extra, ref = _pool(cfg, store)
        elif group.source == "canonical":
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
        if pool_extra is not None:
            manifest["pool"] = pool_extra
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
    # ``stage`` è obbligatorio nella configurazione: si usa ``execute``, di cui lo script riusa
    # gli executor. Gli override da riga di comando vengono dopo e prevalgono (es. paths=local).
    cfg = load_experiment(["paths=cluster", "stage=execute", *args.overrides])
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
