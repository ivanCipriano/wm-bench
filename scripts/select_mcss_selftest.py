"""Selezione CodeNet per la verifica della Tabella 3 di MCSS (PIPELINE_metodo.md v1.2 §4.4.2).

Criteri (pipeline v1.2 e decisioni dell'utente del 13 novembre 2026, ``docs/deviations.md``):

- tabella del profilo ``mcss`` **con le variabili di modulo** (``--canonical paper
  --include-params --module-level on --channels mcss8``); ``n_bits_v1`` = siti strutturali +
  2 · variabili, come la fascia D32 del paper;
- fonte: il pool ``L2_codenet_pool`` di ``scripts/export_inventory_inputs.py`` (sottomissioni
  umane CodeNet Python, già senza canoniche e negativi di test), da cui si tolgono anche le righe
  dei problemi di test (``split == "test"``: sottomissioni di riserva e quasi duplicati) e quelle
  dei problemi nello stesso cluster ``identical_problem_clusters`` di un problema di test
  (``L2_codenet_pool.test_clusters.json``, scritto dall'export);
- fascia **32–39 bit**; se vi cadono meno di ``MIN_FILES`` = 100 file ammessi, si allarga a
  ``n_bits_v1 >= 32``;
- al più ``MAX_FILES`` = 250 file, in ordine ``derive_seed(global_seed, "mcss-selftest",
  sample_id)`` (``global_seed`` dal manifest del pool); **una sottomissione per problema** (la
  prima nell'ordine) quando i problemi distinti fra i candidati sono almeno 100, altrimenti si
  prendono a turno la prima, la seconda, ... sottomissione di ogni problema;
- con meno di 100 file scelti il riepilogo lo segnala: la pipeline prevede allora di valutare
  altre sottomissioni dei soli problemi di sviluppo (estrazione non ancora decisa);
- criterio di accettazione: media di ``n_bits_v1`` entro ±10% di 34,6; le medie di variabili e
  siti strutturali si riportano con lo scostamento da 11,9 e 10,9, senza esito.

Gira nell'ambiente **posthoc** (Python >= 3.9, libreria standard e ``bench_contracts``). Uscite:

- ``--out``: JSONL della selezione, ordinato per ``sample_id``, con il codice e i conteggi;
- ``--report``: riepilogo (pool, esclusioni contate, fascia usata, selezione per ruolo, medie,
  siti di ``multimark`` base sugli stessi file, elenco dei ``sample_id``).

Un file d'uscita già presente con lo stesso contenuto non si riscrive; con contenuto diverso lo
script si ferma senza toccarlo.

Uso (vedi ``scripts/inventory_sites.sh selftest``)::

    <posthoc>/bin/python scripts/select_mcss_selftest.py --pool POOL.jsonl \\
        --mcss-table T.canonical-paper_guards-on_params-on_modlevel-on.csv \\
        --base-table T.canonical-kind_guards-on_params-off.csv --out SEL.jsonl --report REP.txt
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bench_contracts import derive_seed

BAND = (32, 39)  # fascia D32 di MCSS, in bit
MAX_FILES = 250
MIN_FILES = 100  # minimo utile (§4.4 punto 2): sotto si allarga a n_bits_v1 >= 32
SEED_LABEL = "mcss-selftest"
TEST_CLUSTERS_SUFFIX = ".test_clusters.json"  # come scripts/export_inventory_inputs.py
# Tabella 3 di MCSS (p. 8), fascia 32-39 bit: medie per file.
PAPER_BITS, PAPER_VARS, PAPER_STRUCTURAL = 34.6, 11.9, 10.9
BITS_TOLERANCE = 0.10  # criterio di accettazione: media di n_bits_v1 entro ±10% di 34,6
# Soglie dei criteri di P2 per multimark sugli stessi file (B = 32; TPR con N_reg = 1 da 13 siti).
MULTIMARK_THRESHOLDS = (32, 13)


class SelectionError(RuntimeError):
    """Ingresso incoerente o uscita già presente con contenuto diverso."""


def read_table(path: Path) -> dict[str, dict[str, str]]:
    """Tabella per campione di ``inventory_sites.py`` indicizzata per ``sample_id``."""
    with path.open(encoding="utf-8", newline="") as fh:
        return {row["sample_id"]: row for row in csv.DictReader(fh)}


def read_pool(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                out[rec["sample_id"]] = rec
    return out


def pool_seed(pool: Path) -> int:
    """``global_seed`` dal manifest del pool (``<pool>.manifest.json``)."""
    manifest = pool.with_name(pool.name + ".manifest.json")
    try:
        return int(json.loads(manifest.read_text(encoding="utf-8"))["pool"]["global_seed"])
    except (OSError, KeyError, ValueError) as exc:
        raise SelectionError(f"cannot read global_seed from {manifest}: {exc}") from exc


def default_test_clusters(pool: Path) -> Path:
    """``L2_codenet_pool.test_clusters.json`` accanto al pool."""
    return pool.with_name(pool.stem + TEST_CLUSTERS_SUFFIX)


def read_test_clusters(path: Path) -> set[str]:
    """``pid`` dei problemi non di test nello stesso cluster di un problema di test."""
    try:
        return set(json.loads(path.read_text(encoding="utf-8"))["same_cluster_as_test"])
    except (OSError, KeyError, ValueError) as exc:
        raise SelectionError(f"cannot read same_cluster_as_test from {path}: {exc}") from exc


def _int(row: dict[str, str], key: str) -> int:
    return int(row[key])


def _pid(rec: dict[str, Any]) -> str:
    return str(rec["problem_key"]).split("/", 1)[-1]


def eligible(
    pool: dict[str, dict[str, Any]],
    mcss: dict[str, dict[str, str]],
    cluster_pids: set[str],
) -> tuple[list[str], dict[str, int]]:
    """``sample_id`` ammessi (ordinati) e conteggi degli esclusi, nell'ordine dei filtri:
    ``split == "test"``, stesso cluster di un problema di test, non analizzati."""
    kept: list[str] = []
    excluded = {"split_test": 0, "test_cluster": 0, "parse_error": 0}
    for sid in sorted(pool):
        rec = pool[sid]
        if rec.get("split") == "test":
            excluded["split_test"] += 1
        elif _pid(rec) in cluster_pids:
            excluded["test_cluster"] += 1
        elif mcss[sid]["parse_ok"] != "True":
            excluded["parse_error"] += 1
        else:
            kept.append(sid)
    return kept, excluded


def candidates(
    mcss: dict[str, dict[str, str]], ids: Sequence[str]
) -> tuple[list[str], list[str], bool]:
    """``(fascia, candidati, allargata)``: righe in 32-39 bit e, se sono meno di ``MIN_FILES``,
    candidati con ``n_bits_v1 >= 32``."""
    band = [s for s in ids if BAND[0] <= _int(mcss[s], "n_bits_v1") <= BAND[1]]
    if len(band) >= MIN_FILES:
        return band, band, False
    return band, [s for s in ids if _int(mcss[s], "n_bits_v1") >= BAND[0]], True


def select(
    pool: dict[str, dict[str, Any]],
    ids: Sequence[str],
    seed: int,
    limit: int = MAX_FILES,
    minimum: int = MIN_FILES,
) -> tuple[list[str], bool]:
    """``(scelti in ordine di sample_id, una per problema)``.

    Ordine ``derive_seed(seed, "mcss-selftest", sample_id)``; ogni sottomissione ha il suo rango
    nel problema (0 = la prima nell'ordine). Con almeno ``minimum`` problemi distinti si tengono
    solo i ranghi 0, altrimenti si ordina per (rango, seme): una a testa, poi la seconda, ecc.
    """
    ordered = sorted(ids, key=lambda sid: (derive_seed(seed, SEED_LABEL, sid), sid))
    rank: dict[str, int] = {}
    seen: defaultdict[str, int] = defaultdict(int)
    for sid in ordered:
        pid = _pid(pool[sid])
        rank[sid] = seen[pid]
        seen[pid] += 1
    one_per_problem = len(seen) >= minimum
    if one_per_problem:
        picked = [s for s in ordered if rank[s] == 0]
    else:
        position = {s: i for i, s in enumerate(ordered)}
        picked = sorted(ordered, key=lambda s: (rank[s], position[s]))
    return sorted(picked[:limit]), one_per_problem


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _col(rows: Sequence[dict[str, str]], key: str) -> list[int]:
    return [int(r.get(key) or 0) for r in rows]


def _rel(value: float, paper: float) -> str:
    if math.isnan(value):
        return "n/a"
    return f"{value:.2f} vs {paper} ({(value - paper) / paper:+.1%})"


def _criterion(value: float) -> str:
    if math.isnan(value):
        return "n/a"
    ok = abs(value - PAPER_BITS) / PAPER_BITS <= BITS_TOLERANCE
    return f"{_rel(value, PAPER_BITS)}: {'PASS' if ok else 'FAIL'} (±{BITS_TOLERANCE:.0%})"


def report(
    pool: dict[str, dict[str, Any]],
    mcss: dict[str, dict[str, str]],
    base: dict[str, dict[str, str]],
    sel: dict[str, Any],
    seed: int,
) -> str:
    """Testo del riepilogo (vedi docstring del modulo)."""
    ids, excluded = sel["eligible"], sel["excluded"]
    band, cands, chosen = sel["band"], sel["candidates"], sel["chosen"]
    sel_m = [mcss[s] for s in chosen]
    sel_b = [base[s] for s in chosen]
    used = (
        f"n_bits_v1 >= {BAND[0]} (widened: fewer than {MIN_FILES} files in {BAND[0]}-{BAND[1]})"
        if sel["widened"]
        else f"{BAND[0]} <= n_bits_v1 <= {BAND[1]}"
    )
    roles = Counter(str(pool[s].get("role")) for s in chosen)
    lines = [
        "MCSS self-test selection (PIPELINE_metodo.md v1.2 §4.4 point 2)",
        "mcss profile: canonical paper, params on, module-level variables on, channels mcss8",
        f"order: derive_seed({seed}, {SEED_LABEL!r}, sample_id); max {MAX_FILES} files, "
        f"minimum {MIN_FILES}",
        "",
        f"pool files: {len(pool)} (test canonicals and negatives already excluded by the export)",
        f"excluded split == test: {excluded['split_test']}",
        "excluded same identical_problem_clusters cluster as a test problem: "
        f"{excluded['test_cluster']}",
        f"excluded parse errors: {excluded['parse_error']}",
        f"eligible files: {len(ids)} (problems: {len({_pid(pool[s]) for s in ids})})",
        f"eligible files with n_bits_v1 >= {BAND[0]}: "
        f"{sum(1 for s in ids if _int(mcss[s], 'n_bits_v1') >= BAND[0])}",
        f"eligible files in {BAND[0]}-{BAND[1]}: {len(band)}",
        f"band used: {used}",
        f"candidates: {len(cands)} (problems: {len({_pid(pool[s]) for s in cands})})",
        f"one submission per problem: {'yes' if sel['one_per_problem'] else 'no'}",
        f"selected: {len(chosen)}",
        f"distinct problems among selected: {len({_pid(pool[s]) for s in chosen})}",
    ]
    if len(chosen) < MIN_FILES:
        lines.append(
            f"WARNING: fewer than {MIN_FILES} files: §4.4 point 2 asks whether more CodeNet "
            "Python submissions of development problems can be extracted"
        )
    lines += [
        "",
        "selected by role: " + ", ".join(f"{k}={v}" for k, v in sorted(roles.items())),
        "",
        "mcss profile on selected files (Table 3 of MCSS, band 32-39 bit):",
        "  mean n_bits_v1 (criterion): " + _criterion(_mean(_col(sel_m, "n_bits_v1"))),
        "  mean variables:             " + _rel(_mean(_col(sel_m, "n_vars")), PAPER_VARS),
        "  mean structural sites:      "
        + _rel(_mean(_col(sel_m, "n_sites_structural")), PAPER_STRUCTURAL),
        f"  mean module variables:      {_mean(_col(sel_m, 'n_vars_module')):.2f}",
        "",
        "multimark base profile on the same files (canonical kind, params off, extended):",
        f"  mean n_sites_total:    {_mean(_col(sel_b, 'n_sites_total')):.2f}",
        *(
            f"  files with n_sites_total >= {th}: "
            f"{sum(1 for v in _col(sel_b, 'n_sites_total') if v >= th)}"
            for th in MULTIMARK_THRESHOLDS
        ),
        "",
        "selected sample_id:",
        *(f"  {s}" for s in chosen),
    ]
    return "\n".join(lines) + "\n"


def selection_bytes(
    pool: dict[str, dict[str, Any]],
    mcss: dict[str, dict[str, str]],
    base: dict[str, dict[str, str]],
    chosen: list[str],
) -> bytes:
    out = []
    for sid in chosen:
        rec = pool[sid]
        out.append(
            json.dumps(
                {
                    "sample_id": sid,
                    "problem_key": rec["problem_key"],
                    "role": rec.get("role"),
                    "split": rec["split"],
                    "loc": rec["loc"],
                    "mcss_n_bits_v1": _int(mcss[sid], "n_bits_v1"),
                    "mcss_n_vars": _int(mcss[sid], "n_vars"),
                    "mcss_n_vars_module": int(mcss[sid].get("n_vars_module") or 0),
                    "mcss_n_sites_structural": _int(mcss[sid], "n_sites_structural"),
                    "multimark_n_sites_total": _int(base[sid], "n_sites_total"),
                    "code": rec["code"],
                },
                ensure_ascii=False,
            )
        )
    return "".join(line + "\n" for line in out).encode("utf-8")


def write_if_new(path: Path, data: bytes) -> str:
    """Scrittura atomica; ``"unchanged"`` se c'è già lo stesso contenuto.

    Raises:
        SelectionError: se il file esiste con contenuto diverso (non si sovrascrive).
    """
    if path.exists():
        if hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest():
            return "unchanged"
        raise SelectionError(f"{path} exists with different content: move it away and rerun")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return "written"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--pool", required=True, type=Path, help="L2_codenet_pool.jsonl")
    p.add_argument("--mcss-table", required=True, type=Path, help="pool table, mcss_mod profile")
    p.add_argument("--base-table", required=True, type=Path, help="pool table, multimark base")
    p.add_argument("--test-clusters", type=Path, help="default: <pool stem>.test_clusters.json")
    p.add_argument("--out", required=True, type=Path, help="selection JSONL")
    p.add_argument("--report", type=Path, help="text report (default: stdout)")
    args = p.parse_args(argv)
    try:
        pool = read_pool(args.pool)
        mcss, base = read_table(args.mcss_table), read_table(args.base_table)
        if set(mcss) != set(pool) or set(base) != set(pool):
            raise SelectionError("pool and tables do not contain the same sample_id")
        seed = pool_seed(args.pool)
        cluster_pids = read_test_clusters(args.test_clusters or default_test_clusters(args.pool))
        ids, excluded = eligible(pool, mcss, cluster_pids)
        band, cands, widened = candidates(mcss, ids)
        chosen, one_per_problem = select(pool, cands, seed)
        sel = {
            "eligible": ids,
            "excluded": excluded,
            "band": band,
            "candidates": cands,
            "widened": widened,
            "chosen": chosen,
            "one_per_problem": one_per_problem,
        }
        write_if_new(args.out, selection_bytes(pool, mcss, base, chosen))
        text = report(pool, mcss, base, sel, seed)
        if args.report:
            write_if_new(args.report, text.encode("utf-8"))
        else:
            sys.stdout.write(text)
    except SelectionError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] selected {len(chosen)} of {len(cands)} candidates -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
