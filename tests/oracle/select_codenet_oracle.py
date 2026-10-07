"""Selezione dei 3 problemi CodeNet per l'oracle di MCGMark su codice lungo (bench-core, cluster).

    python tests/oracle/select_codenet_oracle.py      # scripts/oracle_method.sh mcgmark select

Criteri (decisione dell'utente del 7 ottobre 2026; cluster_info §8.6):
- casi di input/output in ``derived/input_output/data/<pid>/``, non in ``no_solutions.txt``;
- non in un cluster di ``identical_problem_clusters`` (nessun gemello nella selezione della M9);
- descrizione presente;
- almeno 5 sottomissioni ``Accepted`` in Python, con mediana di ``code_size`` ≥ 1000 byte: codice
  abbastanza lungo da arrivare alle 24 posizioni di un ciclo di MCGMark.
Ordine deterministico ``derive_seed(global_seed, "codenet-oracle-mcgmark", pid)``: i primi 3 che
soddisfano i criteri. Scrive ``configs/dataset/codenet_excluded.yaml`` (da versionare).
"""

from __future__ import annotations

import csv
import re
import statistics
import sys

import yaml
from bench.config.builder import load_experiment, repo_root
from bench.data.codenet_exclusions import load_excluded
from bench.domain.ids import derive_seed

N_PROBLEMS = 3
MIN_ACCEPTED = 5
MIN_MEDIAN_SIZE = 1000
PID = re.compile(r"p\d{5}")
HEADER = """\
# Problemi CodeNet esclusi dalla selezione dei 250 problemi della Milestone 9 (decisione dell'utente
# del 7 ottobre 2026). Generato da tests/oracle/select_codenet_oracle.py (scripts/oracle_method.sh
# mcgmark select) e versionato: non va modificato a mano.
# I problemi "oracle_mcgmark" servono solo alla verifica di MCGMark su codice lungo: non entrano in
# nessuna metrica né nella scelta degli iperparametri.
"""


def main() -> int:
    cfg = load_experiment(["paths=cluster", "stage=watermark"])
    spec = cfg.datasets["codenet"]
    out = spec.file("excluded")
    if load_excluded(out):
        sys.stdout.write(f"{out} already lists problems: nothing to do\n")
        return 0
    no_solutions = set(PID.findall(spec.file("no_solutions").read_text(encoding="utf-8")))
    clustered = set(PID.findall(spec.file("identical_clusters").read_text(encoding="utf-8")))
    io_dir, meta_dir = spec.dirs["io_data"], spec.dirs["metadata"]
    desc_dir = spec.dirs["problem_descriptions"]
    candidates = sorted(
        (p.name for p in io_dir.iterdir() if PID.fullmatch(p.name)),
        key=lambda pid: derive_seed(cfg.global_seed, "codenet-oracle-mcgmark", pid),
    )
    chosen: list[dict[str, object]] = []
    for pid in candidates:
        if pid in no_solutions or pid in clustered:
            continue
        if not (io_dir / pid / "input.txt").is_file() or not (desc_dir / f"{pid}.html").is_file():
            continue
        sizes = []
        with (meta_dir / f"{pid}.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["status"] == "Accepted" and row["language"] == "Python":
                    sizes.append(int(row["code_size"]))
        if len(sizes) < MIN_ACCEPTED or statistics.median(sizes) < MIN_MEDIAN_SIZE:
            continue
        chosen.append(
            {
                "problem_key": f"codenet/{pid}",
                "use": "oracle_mcgmark",
                "reason": "usato per l'oracle di MCGMark (verifica su codice lungo)",
                "accepted_python": len(sizes),
                "median_code_size": statistics.median(sizes),
            }
        )
        median = chosen[-1]["median_code_size"]
        sys.stdout.write(f"selected {pid}: {len(sizes)} accepted Python, median {median} B\n")
        if len(chosen) == N_PROBLEMS:
            break
    if len(chosen) < N_PROBLEMS:
        raise SystemExit(f"only {len(chosen)} problems satisfy the criteria")
    body = yaml.safe_dump({"excluded": chosen}, sort_keys=False, allow_unicode=True)
    out.write_text(HEADER + body, encoding="utf-8")
    load_excluded(out)  # verifica del formato
    sys.stdout.write(f"wrote {out.relative_to(repo_root())}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
