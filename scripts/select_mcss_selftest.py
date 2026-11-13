"""Selezione CodeNet per la verifica della Tabella 3 di MCSS (PIPELINE_metodo.md §4.4 punto 2).

Decisione dell'utente del 13 novembre 2026 (criteri di accettazione di P2, ``docs/deviations.md``):
fino a 250 file della fascia **32–39 bit**, cioè ``32 <= n_bits_v1 <= 39`` con il profilo ``mcss``
(forme ``paper``, parametri inclusi; ``n_bits_v1`` = siti strutturali + 2 · variabili, come la
fascia D32 del paper). La fonte sono le sottomissioni umane CodeNet Python che non sono né negativi
né canoniche dei problemi di test (gruppo ``L2_codenet_pool`` di
``scripts/export_inventory_inputs.py``). Se nella fascia ci
sono meno di 250 file si prendono tutti e il riepilogo ne riporta il numero; se sono di più, si
prendono i primi 250 nell'ordine ``derive_seed(global_seed, "mcss-selftest", sample_id)``, con
``global_seed`` letto dal manifest del pool (scritto da bench-core).

Gira nell'ambiente **posthoc** (Python >= 3.9, libreria standard e ``bench_contracts``). Ingressi:
il JSONL del pool e le due tabelle di ``scripts/inventory_sites.py`` sul pool, profilo ``mcss`` e
base di ``multimark``. Uscite:

- ``--out``: JSONL della selezione, ordinato per ``sample_id``, con il codice e i conteggi dei due
  profili;
- ``--report``: riepilogo (dimensioni del pool, fascia, selezione per ruolo, medie di variabili e
  siti strutturali confrontate con 11,9 e 10,9 della Tabella 3, siti di ``multimark`` sui file
  scelti, elenco dei ``sample_id``).

Un file d'uscita già presente con lo stesso contenuto non si riscrive; con contenuto diverso lo
script si ferma senza toccarlo.

Uso (vedi ``scripts/inventory_sites.sh selftest``)::

    <posthoc>/bin/python scripts/select_mcss_selftest.py --pool POOL.jsonl \\
        --mcss-table T.canonical-paper_guards-on_params-on.csv \\
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
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bench_contracts import derive_seed

BAND = (32, 39)  # fascia D32 di MCSS, in bit
MAX_FILES = 250
SEED_LABEL = "mcss-selftest"
# Tabella 3 di MCSS (p. 8), fascia 32-39 bit: medie per file.
PAPER_VARS, PAPER_STRUCTURAL = 11.9, 10.9
TOLERANCE = 0.30  # ±30% (PIPELINE §4.4 punto 2; criterio di P2)
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


def _int(row: dict[str, str], key: str) -> int:
    return int(row[key])


def in_band(row: dict[str, str]) -> bool:
    """Campione analizzato con ``n_bits_v1`` nella fascia (profilo mcss)."""
    return row["parse_ok"] == "True" and BAND[0] <= _int(row, "n_bits_v1") <= BAND[1]


def select(
    mcss: dict[str, dict[str, str]], seed: int, limit: int = MAX_FILES
) -> tuple[list[str], list[str]]:
    """``(fascia, scelti)``: ``sample_id`` nella fascia e i primi ``limit`` nell'ordine del seme,
    restituiti in ordine di ``sample_id``."""
    band = sorted(sid for sid, row in mcss.items() if in_band(row))
    ordered = sorted(band, key=lambda sid: (derive_seed(seed, SEED_LABEL, sid), sid))
    return band, sorted(ordered[:limit])


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _col(rows: Sequence[dict[str, str]], key: str) -> list[int]:
    return [_int(r, key) for r in rows]


def _vs_paper(value: float, paper: float) -> str:
    if math.isnan(value):
        return "n/a"
    rel = (value - paper) / paper
    verdict = "within" if abs(rel) <= TOLERANCE else "OUTSIDE"
    return f"{value:.2f} vs {paper} ({rel:+.1%}, {verdict} ±{TOLERANCE:.0%})"


def report(
    pool: dict[str, dict[str, Any]],
    mcss: dict[str, dict[str, str]],
    base: dict[str, dict[str, str]],
    band: list[str],
    chosen: list[str],
    seed: int,
) -> str:
    """Testo del riepilogo (vedi docstring del modulo)."""
    parsed = [r for r in mcss.values() if r["parse_ok"] == "True"]
    ge32 = sum(1 for r in parsed if _int(r, "n_bits_v1") >= BAND[0])
    sel_m = [mcss[s] for s in chosen]
    sel_b = [base[s] for s in chosen]
    lines = [
        "MCSS self-test selection (PIPELINE §4.4 point 2; decision of 13 November 2026)",
        f"band: {BAND[0]} <= n_bits_v1 <= {BAND[1]} (mcss profile: canonical paper, params on)",
        f"order: derive_seed({seed}, {SEED_LABEL!r}, sample_id); max {MAX_FILES} files",
        "",
        f"pool files: {len(pool)} (parsed: {len(parsed)})",
        f"pool files with n_bits_v1 >= {BAND[0]}: {ge32}",
        f"files in band: {len(band)}",
        f"selected: {len(chosen)}"
        + (f" (fewer than {MAX_FILES}: all files in band)" if len(chosen) < MAX_FILES else ""),
        f"distinct problems among selected: {len({pool[s]['problem_key'] for s in chosen})}",
        "",
        "selected by role: "
        + ", ".join(
            f"{k}={v}" for k, v in sorted(Counter(pool[s].get("role") for s in chosen).items())
        ),
        "",
        "mcss profile on selected files (Table 3 of MCSS, band 32-39 bit):",
        "  mean variables:        " + _vs_paper(_mean(_col(sel_m, "n_vars")), PAPER_VARS),
        "  mean structural sites: "
        + _vs_paper(_mean(_col(sel_m, "n_sites_structural")), PAPER_STRUCTURAL),
        f"  mean n_bits_v1:        {_mean(_col(sel_m, 'n_bits_v1')):.2f}",
        "",
        "multimark base profile on the same files (canonical kind, params off):",
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
    p.add_argument("--mcss-table", required=True, type=Path, help="pool table, mcss profile")
    p.add_argument("--base-table", required=True, type=Path, help="pool table, multimark base")
    p.add_argument("--out", required=True, type=Path, help="selection JSONL")
    p.add_argument("--report", type=Path, help="text report (default: stdout)")
    args = p.parse_args(argv)
    try:
        pool = read_pool(args.pool)
        mcss, base = read_table(args.mcss_table), read_table(args.base_table)
        if set(mcss) != set(pool) or set(base) != set(pool):
            raise SelectionError("pool and tables do not contain the same sample_id")
        seed = pool_seed(args.pool)
        band, chosen = select(mcss, seed)
        write_if_new(args.out, selection_bytes(pool, mcss, base, chosen))
        text = report(pool, mcss, base, band, chosen, seed)
        if args.report:
            write_if_new(args.report, text.encode("utf-8"))
        else:
            sys.stdout.write(text)
    except SelectionError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(
        f"[OK] selected {len(chosen)} of {len(band)} files in band -> {args.out}", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
