"""Selezione dei 250 problemi CodeNet del Livello 2 (Milestone 9; cluster_info §8.6, TODO 1 e 11).

Uso (con bench-core), in tre passi:
    python scripts/codenet_select.py candidates   # nodo di login: criteri su metadati e testo
    # estrazione selettiva: tar -xzf Project_CodeNet.tar.gz -T extract_list.txt (job su defq)
    python scripts/codenet_select.py validate     # job CPU: canonica e 5 negativi nella sandbox
    python scripts/codenet_select.py finalize     # nodo di login: scrive codenet_selected.yaml

Criteri e ordine (decisioni dell'utente del 13 ottobre 2026; ``bench.data.codenet``):
0. tutti i problemi di ``problem_list.csv``;
1. caso di I/O presente e problema non in ``no_solutions.txt``;
2. non esclusi (oracle di MCGMark, ``codenet_excluded.yaml``);
3. descrizione in inglese (nessun carattere giapponese dopo la conversione);
4. nessuna immagine nella descrizione;
5. versioni compatibili: almeno 6 accettate verificate, di utenti distinti e non quasi duplicate,
   in ciascuno dei 4 linguaggi;
6. validazione nella sandbox: dopo la deduplicazione per impronta del codice, la canonica e i 5
   negativi passano l'unico caso di I/O in tutti e 4 i linguaggi (24 esecuzioni);
7. al più un problema per cluster di ``identical_problem_clusters``;
8. i primi 250 per ``derive_seed(global_seed, "codenet-select", pid)``.

I conteggi dopo ogni criterio vanno in ``codenet_selected.yaml`` (e nel manifest della fase
``prepare_data`` di L2). Con meno di 250 problemi idonei ``finalize`` si ferma con exit code 1 e
non scrive nulla: il criterio da allentare lo decide l'utente.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from bench.config.builder import load_experiment, repo_root
from bench.data.codenet import (
    N_PER_LANGUAGE,
    Candidate,
    CodeNetFiles,
    Submission,
    dedup_codes,
    final_selection,
    find_candidates,
    problem_clusters,
)
from bench.data.codenet_exclusions import check_not_excluded, load_excluded
from bench.domain.enums import ExecStatus, Language, Level
from bench.domain.models import Problem

N_PROBLEMS = 250
HEADER = """\
# Problemi CodeNet del Livello 2 (Milestone 9; cluster_info §8.6, TODO 1 e 11). Generato da
# scripts/codenet_select.py finalize e versionato: non va modificato a mano.
# Per ogni problema e linguaggio: la soluzione canonica e i 5 negativi umani (accettate
# di utenti distinti). "funnel": problemi rimasti dopo ogni criterio, nell'ordine di applicazione.
"""


def _setup() -> tuple[Any, CodeNetFiles, Path]:
    cfg = load_experiment(["paths=cluster", "stage=prepare_data"])
    spec = cfg.datasets["codenet"]
    work = spec.dirs["work"]
    work.mkdir(parents=True, exist_ok=True)
    return cfg, CodeNetFiles.from_spec(spec), work


def _load_candidates(work: Path) -> tuple[list[Candidate], dict[str, Any]]:
    data = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
    return [Candidate.from_dict(c) for c in data["candidates"]], data


# --------------------------------------------------------------------------- candidates
def cmd_candidates() -> int:
    cfg, files, work = _setup()
    excluded = load_excluded(cfg.datasets["codenet"].file("excluded"))
    candidates, funnel, stats = find_candidates(files, cfg.global_seed, excluded)
    payload = {
        "funnel": funnel.as_list(),
        "stats": stats,
        "candidates": [c.as_dict() for c in candidates],
    }
    (work / "candidates.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    paths = sorted(
        f"Project_CodeNet/data/{s.relpath(c.pid)}"
        for c in candidates
        for subs in c.submissions.values()
        for s in subs
    )
    (work / "extract_list.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
    for step in funnel.as_list():
        sys.stdout.write(f"{step['remaining']:>6}  {step['criterion']}\n")
    sys.stdout.write(f"stats: {json.dumps(stats)}\n")
    sys.stdout.write(
        f"wrote {work / 'candidates.json'} and {work / 'extract_list.txt'} "
        f"({len(paths)} files)\n"
    )
    return 0


# --------------------------------------------------------------------------- validate
def _codes(root: Path, pid: str, subs: list[Submission]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for sub in subs:
        path = root / sub.relpath(pid)
        out[sub.submission_id] = (
            path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        )
    return out


def _validate_one(
    executors: dict[Language, Any], data_root: Path, cand: Candidate, lang: Language
) -> dict[str, Any]:
    from bench.execution.executors import SampleToRun

    subs = cand.submissions[lang]
    codes = _codes(data_root, cand.pid, subs)
    chosen, dropped = dedup_codes(subs, codes)
    record: dict[str, Any] = {
        "pid": cand.pid,
        "language": str(lang),
        "chosen": [s.submission_id for s in chosen],
        "dropped": dropped,
        "statuses": [],
    }
    if len(chosen) < N_PER_LANGUAGE:
        record["ok"] = False
        record["reason"] = "fewer than 6 distinct submissions after code deduplication"
        return record
    problem = Problem(
        problem_key=f"codenet/{cand.pid}",
        dataset="codenet",
        level=Level.L2,
        language=lang,
        split=None,
        prompt_text="",
        entry_point=None,
        canonical_solution=None,
        test_ref=cand.pid,
        contamination_risk=False,
        loc_to_generate=None,
    )
    samples = [
        SampleToRun(s.submission_id, problem.problem_key, i, str(codes[s.submission_id]))
        for i, s in enumerate(chosen)
    ]
    results = executors[lang].run_problem(problem, samples)
    record["statuses"] = [str(r.status) for r in results]
    record["stderr"] = [r.stderr_tail for r in results if r.status is not ExecStatus.PASSED][:2]
    record["ok"] = all(r.status is ExecStatus.PASSED for r in results)
    if not record["ok"]:
        record["reason"] = "not all submissions pass the input/output case"
    return record


def cmd_validate() -> int:
    from bench.execution import sandbox as sandbox_mod
    from bench.execution.executors import CodeNetExecutor
    from bench.pipeline.stages.execute import job_cpus

    cfg, _, work = _setup()
    candidates, _ = _load_candidates(work)
    out = work / "validation.jsonl"
    done: set[tuple[str, str]] = set()
    if out.is_file():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["pid"], rec["language"]))
    sandbox = sandbox_mod.make_sandbox(cfg.execution)
    root = sandbox.workdir_root(cfg.paths.tmp / "execute")
    root.mkdir(parents=True, exist_ok=True)
    executors = {lang: CodeNetExecutor(cfg, sandbox, lang, root) for lang in Language}
    data_root = cfg.datasets["codenet"].dirs["data"]
    tasks = [(c, lang) for c in candidates for lang in Language if (c.pid, str(lang)) not in done]
    workers = job_cpus(cfg)
    sys.stdout.write(
        f"{len(tasks)} (problem, language) to validate, {len(done)} already done, "
        f"{workers} workers\n"
    )
    with out.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(_validate_one, executors, data_root, c, lang) for c, lang in tasks]
        for i, future in enumerate(as_completed(futures), start=1):
            handle.write(json.dumps(future.result()) + "\n")
            handle.flush()
            if i % 200 == 0:
                sys.stdout.write(f"  {i}/{len(tasks)}\n")
                sys.stdout.flush()
    sys.stdout.write(f"wrote {out}\n")
    return 0


# --------------------------------------------------------------------------- finalize
def _read_validation(work: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    path = work / "validation.jsonl"
    if not path.is_file():
        raise SystemExit(f"{path} not found: run 'validate' first")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            records[(rec["pid"], rec["language"])] = rec
    return records


def _failure_summary(candidates: list[Candidate], records: dict[tuple[str, str], Any]) -> str:
    reasons: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for c in candidates:
        for lang in Language:
            rec = records.get((c.pid, str(lang)))
            if rec is None:
                reasons[f"{lang}: not validated"] += 1
            elif not rec["ok"]:
                reasons[f"{lang}: {rec.get('reason')}"] += 1
                statuses.update(f"{lang}:{s}" for s in rec["statuses"] if s != "PASSED")
    lines = [f"  {n:>5}  {r}" for r, n in sorted(reasons.items())]
    lines += [f"  {n:>5}  non-passing status {s}" for s, n in sorted(statuses.items())]
    return "\n".join(lines)


def cmd_finalize() -> int:
    cfg, files, work = _setup()
    candidates, data = _load_candidates(work)
    records = _read_validation(work)
    valid = {
        c.pid
        for c in candidates
        if all(records.get((c.pid, str(lang)), {}).get("ok") for lang in Language)
    }
    selected, steps = final_selection(candidates, valid, problem_clusters(files), N_PROBLEMS)
    funnel = data["funnel"] + [{"criterion": name, "remaining": n} for name, n in steps]
    for step in funnel:
        sys.stdout.write(f"{step['remaining']:>6}  {step['criterion']}\n")
    sys.stdout.write("failures in the sandbox validation (problem-language pairs):\n")
    sys.stdout.write(_failure_summary(candidates, records) + "\n")
    if len(selected) < N_PROBLEMS:
        sys.stdout.write(
            f"STOP: only {len(selected)} eligible problems (< {N_PROBLEMS}); nothing written. "
            "Choose which criterion to relax.\n"
        )
        return 1
    excluded = load_excluded(cfg.datasets["codenet"].file("excluded"))
    check_not_excluded([f"codenet/{c.pid}" for c in selected], excluded)
    problems = []
    for c in selected:
        entry: dict[str, Any] = {
            "problem_key": f"codenet/{c.pid}",
            "rank": c.rank,
            "source": c.source,
            "submissions": {},
        }
        for lang in Language:
            by_id = {s.submission_id: s for s in c.submissions[lang]}
            chosen = [by_id[sid] for sid in records[(c.pid, str(lang))]["chosen"]]
            entry["submissions"][str(lang)] = {
                "canonical": chosen[0].as_dict(),
                "negatives": [s.as_dict() for s in chosen[1:]],
            }
        problems.append(entry)
    body = {
        "seed_labels": {"problems": "codenet-select", "submissions": "codenet-sub"},
        "funnel": funnel,
        "problems": problems,
    }
    out = cfg.datasets["codenet"].file("selected")
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100)
    out.write_text(HEADER + text, encoding="utf-8")
    sys.stdout.write(f"wrote {out.relative_to(repo_root())}: {len(problems)} problems\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["candidates", "validate", "finalize"])
    args = parser.parse_args()
    return {"candidates": cmd_candidates, "validate": cmd_validate, "finalize": cmd_finalize}[
        args.step
    ]()


if __name__ == "__main__":
    sys.exit(main())
