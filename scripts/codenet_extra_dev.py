"""Lista delle sottomissioni CodeNet Python in più dei problemi di sviluppo (PIPELINE §4.4.2).

Decisione K dell'utente del 15 novembre 2026: per la verifica della Tabella 3 di ``mcss`` si
estraggono altre sottomissioni umane Python, **solo** dei problemi di L2 con ``split == "dev"``,
con gli stessi filtri della selezione M9 (``bench.data.codenet.pick_submissions``: ``Accepted``,
verificate, versione compatibile con la sandbox, utenti distinti, niente quasi duplicati di
``derived/duplicates``, ordine ``derive_seed(global_seed, "codenet-sub", pid, "python", sid)``)
ma con ``limit`` = 60 invece di 10. Le prime 10 della lista sono quelle già estratte in M9
(stesso ordine, stessi filtri): si tolgono le sottomissioni già presenti in
``datasets.codenet.dirs.data``, il resto va nella lista per ``tar -xzf ... -T``. Il confronto
per impronta del codice (``code_fingerprint``) richiede il codice, quindi si fa dopo
l'estrazione, nell'export del pool allargato (``export_inventory_inputs.py --groups
L2_codenet_pool_extra --extra-data ...``).

Gira in **bench-core** (job su defq, ``scripts/codenet_extra_dev.sh``). Uscite in ``--work``:

- ``extra_dev_list.txt``: percorsi ``Project_CodeNet/data/<pid>/Python/<sid>.<ext>``, ordinati;
- ``extra_dev_list.json``: conteggi e, per problema, le sottomissioni scelte e quelle già
  presenti.

Un file d'uscita già presente con lo stesso contenuto non si riscrive; con contenuto diverso lo
script si ferma senza toccarlo.

Uso::

    python scripts/codenet_extra_dev.py [--limit 60] [--work DIR] [override Hydra ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.data.codenet import (
    PID,
    SID,
    CodeNetFiles,
    Submission,
    duplicate_index,
    pick_submissions,
    read_csv,
)
from bench.domain.enums import Language

LIMIT = 60  # decisione dell'utente del 15 novembre 2026 (M9: N_EXTRACT = 10)
LIST_NAME = "extra_dev_list.txt"
JSON_NAME = "extra_dev_list.json"
ARCHIVE_PREFIX = "Project_CodeNet/data/"


class ListError(RuntimeError):
    """Ingresso incoerente o uscita già presente con contenuto diverso."""


def plan(
    seed: int,
    dev_pids: Sequence[str],
    metadata: Mapping[str, Sequence[Mapping[str, str]]],
    unverified: set[str],
    duplicates: Mapping[str, int],
    present: set[str],
    limit: int = LIMIT,
) -> tuple[list[str], dict[str, Any]]:
    """Percorsi da estrarre (ordinati) e riepilogo per problema.

    ``metadata``: righe di ``metadata/<pid>.csv``; ``present``: percorsi relativi a
    ``Project_CodeNet/data`` già estratti.
    """
    paths: list[str] = []
    per_problem: dict[str, Any] = {}
    for pid in sorted(dev_pids):
        subs: list[Submission] = pick_submissions(
            seed, pid, Language.PYTHON, metadata[pid], unverified, duplicates, limit
        )
        new = [s for s in subs if s.relpath(pid) not in present]
        old = [s for s in subs if s.relpath(pid) in present]
        mine = {s.relpath(pid) for s in subs}
        stray = sorted(x for x in present if x.startswith(pid + "/") and x not in mine)
        paths += [ARCHIVE_PREFIX + s.relpath(pid) for s in new]
        per_problem[pid] = {
            "picked": len(subs),
            "already_present": [s.submission_id for s in old],
            "to_extract": [s.submission_id for s in new],
            "present_not_picked": stray,
        }
    picked = [v["picked"] for v in per_problem.values()]
    summary = {
        "limit": limit,
        "dev_problems": len(per_problem),
        "picked": sum(picked),
        "already_present": sum(len(v["already_present"]) for v in per_problem.values()),
        "to_extract": len(paths),
        "present_not_picked": sum(len(v["present_not_picked"]) for v in per_problem.values()),
        "problems_below_limit": sum(1 for n in picked if n < limit),
        "picked_per_problem": {
            "min": min(picked, default=0),
            "median": statistics.median(picked) if picked else 0,
            "max": max(picked, default=0),
        },
        "problems": per_problem,
    }
    return sorted(paths), summary


def write_if_new(path: Path, data: bytes) -> str:
    """Scrittura atomica; ``"unchanged"`` se c'è già lo stesso contenuto.

    Raises:
        ListError: se il file esiste con contenuto diverso (non si sovrascrive).
    """
    if path.exists():
        if hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest():
            return "unchanged"
        raise ListError(f"{path} exists with different content: move it away and rerun")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return "written"


def report(summary: Mapping[str, Any], seed: int) -> str:
    """Riepilogo per il log (conteggi, senza l'elenco per problema)."""
    pp = summary["picked_per_problem"]
    lines = [
        "CodeNet extra submissions of the development problems (PIPELINE v1.2 §4.4 point 2)",
        f"filters: bench.data.codenet.pick_submissions, Python, limit {summary['limit']}, "
        f"order derive_seed({seed}, 'codenet-sub', pid, 'python', submission_id)",
        f"development problems (split == dev): {summary['dev_problems']}",
        f"submissions picked: {summary['picked']} "
        f"(per problem min {pp['min']}, median {pp['median']}, max {pp['max']}; "
        f"problems below the limit: {summary['problems_below_limit']})",
        f"already extracted in M9: {summary['already_present']} "
        f"(M9 files of these problems not among the picks: {summary['present_not_picked']})",
        f"to extract: {summary['to_extract']}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--limit", type=int, default=LIMIT)
    p.add_argument("--work", type=Path, help="default: datasets.codenet.dirs.work")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (e.g. paths=local)")
    args = p.parse_args(argv)

    from bench.config.builder import load_experiment
    from bench.pipeline.stages.generate_baseline import problems_ref
    from bench.store.artifact_store import ArtifactStore

    cfg = load_experiment(["paths=cluster", "stage=execute", *args.overrides])
    spec = cfg.datasets["codenet"]
    files = CodeNetFiles.from_spec(spec)
    store = ArtifactStore(Path(cfg.paths.artifacts))
    problems = store.read_table(problems_ref("L2", "python"))
    problems = problems[(problems["dataset"] == "codenet") & (problems["split"] == "dev")]
    dev_pids = sorted(str(k).split("/", 1)[1] for k in problems["problem_key"])
    if not dev_pids or not all(PID.fullmatch(pid) for pid in dev_pids):
        print(f"[ERROR] unexpected development problems: {dev_pids[:5]}", file=sys.stderr)
        return 1
    # Come find_candidates: il file elenca problemi; le eventuali sottomissioni non si usano mai.
    unverified = set(SID.findall(files.unverified.read_text(encoding="utf-8", errors="replace")))
    duplicates = duplicate_index(files.duplicates)
    data = Path(spec.dirs["data"])
    present = {
        f.relative_to(data).as_posix() for pid in dev_pids for f in (data / pid).glob("Python/*")
    }
    metadata = {pid: read_csv(files.metadata / f"{pid}.csv") for pid in dev_pids}
    paths, summary = plan(
        int(cfg.global_seed), dev_pids, metadata, unverified, duplicates, present, args.limit
    )
    work = args.work or Path(spec.dirs["work"])
    try:
        write_if_new(work / LIST_NAME, "".join(x + "\n" for x in paths).encode("utf-8"))
        text = json.dumps(summary, indent=1, sort_keys=True) + "\n"
        write_if_new(work / JSON_NAME, text.encode("utf-8"))
    except ListError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(report(summary, int(cfg.global_seed)))
    sys.stdout.write(f"wrote {work / LIST_NAME} and {work / JSON_NAME}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
