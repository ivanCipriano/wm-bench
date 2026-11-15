"""Lista delle sottomissioni CodeNet Python in più per la verifica di mcss (PIPELINE §4.4.2).

Decisioni dell'utente del 15 novembre 2026 (K e successiva): per la verifica della Tabella 3 di
``mcss`` si estraggono altre sottomissioni umane Python di due gruppi di problemi:

- ``dev``: i problemi di L2 con ``split == "dev"``;
- ``unused``: i problemi candidati della M9 (``candidates.json``) che non sono fra i 250 di L2
  (``split`` nullo nel pool) e non stanno nello stesso cluster ``identical_problem_clusters`` di
  un problema di test (stesso filtro della selezione): non sono usati da nessuna parte nel
  benchmark.

Stessi filtri della selezione M9 (``bench.data.codenet.pick_submissions``: ``Accepted``,
verificate, versione compatibile con la sandbox, utenti distinti, niente quasi duplicati di
``derived/duplicates``, ordine ``derive_seed(global_seed, "codenet-sub", pid, "python", sid)``)
ma con ``limit`` = 60 invece di 10. Le prime 10 della lista sono quelle già estratte in M9
(stesso ordine, stessi filtri): si tolgono le sottomissioni già presenti in
``datasets.codenet.dirs.data``, il resto va in **una sola** lista per ``tar -xzf ... -T`` (un
solo passaggio sull'archivio). Il confronto per impronta del codice (``code_fingerprint``)
richiede il codice, quindi si fa dopo l'estrazione, nell'export del pool allargato
(``export_inventory_inputs.py --groups L2_codenet_pool_extra --extra-data ...``), che assegna i
ruoli ``extra_dev`` ed ``extra_unused``.

Gira in **bench-core** (job su defq, ``scripts/codenet_extra.sh``). Uscite in ``--work``:

- ``extra_list.txt``: percorsi ``Project_CodeNet/data/<pid>/Python/<sid>.<ext>``, ordinati;
- ``extra_list.json``: conteggi per gruppo e, per problema, il gruppo, le sottomissioni scelte
  e quelle già presenti; i problemi ``unused`` tolti perché nel cluster di un test.

Un file d'uscita già presente con lo stesso contenuto non si riscrive; con contenuto diverso lo
script si ferma senza toccarlo.

Uso::

    python scripts/codenet_extra.py [--limit 60] [--work DIR] [override Hydra ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.data.codenet import (
    PID,
    SID,
    CodeNetFiles,
    Submission,
    duplicate_index,
    pick_submissions,
    problem_clusters,
    read_csv,
)
from bench.domain.enums import Language

LIMIT = 60  # decisione dell'utente del 15 novembre 2026 (M9: N_EXTRACT = 10)
LIST_NAME = "extra_list.txt"
JSON_NAME = "extra_list.json"
ARCHIVE_PREFIX = "Project_CodeNet/data/"
GROUPS = ("dev", "unused")


class ListError(RuntimeError):
    """Ingresso incoerente o uscita già presente con contenuto diverso."""


def problem_groups(
    candidates: Iterable[str],
    splits: Mapping[str, str | None],
    clusters: Mapping[str, int],
) -> tuple[dict[str, str], list[str]]:
    """``(pid → gruppo, pid unused tolti)``.

    ``candidates``: ``pid`` di ``candidates.json``; ``splits``: ``pid`` → split dei 250 problemi
    di L2; ``clusters``: ``pid`` → cluster (``problem_clusters``). Un candidato senza split è
    ``unused`` se non sta nel cluster di un problema di test.
    """
    test_clusters = {clusters[p] for p, s in splits.items() if s == "test" and p in clusters}
    groups = {p: "dev" for p, s in splits.items() if s == "dev"}
    dropped = []
    for pid in sorted(set(candidates) - set(splits)):
        if pid in clusters and clusters[pid] in test_clusters:
            dropped.append(pid)
        else:
            groups[pid] = "unused"
    return dict(sorted(groups.items())), dropped


def plan(
    seed: int,
    groups: Mapping[str, str],
    metadata: Mapping[str, Sequence[Mapping[str, str]]],
    unverified: set[str],
    duplicates: Mapping[str, int],
    present: set[str],
    limit: int = LIMIT,
) -> tuple[list[str], dict[str, Any]]:
    """Percorsi da estrarre (ordinati) e riepilogo per gruppo e per problema.

    ``groups``: ``pid`` → ``dev``/``unused``; ``metadata``: righe di ``metadata/<pid>.csv``;
    ``present``: percorsi relativi a ``Project_CodeNet/data`` già estratti.
    """
    paths: list[str] = []
    per_problem: dict[str, Any] = {}
    for pid in sorted(groups):
        subs: list[Submission] = pick_submissions(
            seed, pid, Language.PYTHON, metadata[pid], unverified, duplicates, limit
        )
        new = [s for s in subs if s.relpath(pid) not in present]
        old = [s for s in subs if s.relpath(pid) in present]
        mine = {s.relpath(pid) for s in subs}
        stray = sorted(x for x in present if x.startswith(pid + "/") and x not in mine)
        paths += [ARCHIVE_PREFIX + s.relpath(pid) for s in new]
        per_problem[pid] = {
            "group": groups[pid],
            "picked": len(subs),
            "already_present": [s.submission_id for s in old],
            "to_extract": [s.submission_id for s in new],
            "present_not_picked": stray,
        }
    by_group: dict[str, Any] = {}
    for group in GROUPS:
        items = [v for v in per_problem.values() if v["group"] == group]
        picked = [v["picked"] for v in items]
        by_group[group] = {
            "problems": len(items),
            "picked": sum(picked),
            "already_present": sum(len(v["already_present"]) for v in items),
            "to_extract": sum(len(v["to_extract"]) for v in items),
            "present_not_picked": sum(len(v["present_not_picked"]) for v in items),
            "problems_below_limit": sum(1 for n in picked if n < limit),
            "picked_per_problem": {
                "min": min(picked, default=0),
                "median": statistics.median(picked) if picked else 0,
                "max": max(picked, default=0),
            },
        }
    summary = {
        "limit": limit,
        "to_extract": len(paths),
        "groups": by_group,
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


def report(summary: Mapping[str, Any], seed: int, dropped: Sequence[str] = ()) -> str:
    """Riepilogo per il log (conteggi per gruppo, senza l'elenco per problema)."""
    lines = [
        "CodeNet extra submissions for the mcss self-test (PIPELINE v1.2 §4.4 point 2)",
        f"filters: bench.data.codenet.pick_submissions, Python, limit {summary['limit']}, "
        f"order derive_seed({seed}, 'codenet-sub', pid, 'python', submission_id)",
        "groups: dev = L2 problems with split == dev; unused = M9 candidates not in L2 "
        "(split null) and not in the identical_problem_clusters cluster of a test problem",
        f"unused candidates dropped (same cluster as a test problem): {len(dropped)}",
    ]
    for group, g in summary["groups"].items():
        pp = g["picked_per_problem"]
        lines += [
            f"[{group}] problems: {g['problems']}",
            f"[{group}] submissions picked: {g['picked']} "
            f"(per problem min {pp['min']}, median {pp['median']}, max {pp['max']}; "
            f"problems below the limit: {g['problems_below_limit']})",
            f"[{group}] already extracted in M9: {g['already_present']} "
            f"(M9 files of these problems not among the picks: {g['present_not_picked']})",
            f"[{group}] to extract: {g['to_extract']}",
        ]
    lines.append(f"to extract (one list, one pass over the archive): {summary['to_extract']}")
    if "m9_files_not_candidates" in summary:
        lines.append(
            "problems with M9 Python files, split null, not in candidates.json (not listed): "
            f"{len(summary['m9_files_not_candidates'])}"
        )
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
    work = args.work or Path(spec.dirs["work"])
    problems = store.read_table(problems_ref("L2", "python"))
    problems = problems[problems["dataset"] == "codenet"]
    splits = {
        str(k).split("/", 1)[1]: (None if v is None else str(v))
        for k, v in zip(problems["problem_key"], problems["split"], strict=True)
    }
    cand_path = Path(spec.dirs["work"]) / "candidates.json"
    candidates = [
        str(c["pid"]) for c in json.loads(cand_path.read_text(encoding="utf-8"))["candidates"]
    ]
    if set(splits) - set(candidates):
        print("[ERROR] some L2 problems are not among the M9 candidates", file=sys.stderr)
        return 1
    groups, dropped = problem_groups(candidates, splits, problem_clusters(files))
    if not all(PID.fullmatch(pid) for pid in groups):
        print("[ERROR] unexpected problem ids", file=sys.stderr)
        return 1
    # Come find_candidates: il file elenca problemi; le eventuali sottomissioni non si usano mai.
    unverified = set(SID.findall(files.unverified.read_text(encoding="utf-8", errors="replace")))
    duplicates = duplicate_index(files.duplicates)
    data = Path(spec.dirs["data"])
    present = {
        f.relative_to(data).as_posix() for pid in groups for f in (data / pid).glob("Python/*")
    }
    metadata = {pid: read_csv(files.metadata / f"{pid}.csv") for pid in groups}
    seed = int(cfg.global_seed)
    paths, summary = plan(seed, groups, metadata, unverified, duplicates, present, args.limit)
    summary["unused_dropped_test_cluster"] = dropped
    # Controllo: problemi con file Python di M9 fuori da L2 ma non in candidates.json (non
    # entrano nella lista; il pool di M9 li contiene già).
    on_disk = {f.parent.parent.name for f in data.glob("*/Python/*")}
    summary["m9_files_not_candidates"] = sorted(on_disk - set(candidates) - set(splits))
    try:
        write_if_new(work / LIST_NAME, "".join(x + "\n" for x in paths).encode("utf-8"))
        text = json.dumps(summary, indent=1, sort_keys=True) + "\n"
        write_if_new(work / JSON_NAME, text.encode("utf-8"))
    except ListError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(report(summary, seed, dropped))
    sys.stdout.write(f"wrote {work / LIST_NAME} and {work / JSON_NAME}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
