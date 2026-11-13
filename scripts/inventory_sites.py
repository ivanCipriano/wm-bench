"""Inventario dei siti di multimark sui campioni del benchmark (PIPELINE_metodo.md, P0).

Gira nell'ambiente **posthoc** (Python >= 3.9, solo libreria standard più il pacchetto
``multimark``). Il pacchetto si importa da ``build/patched/multimark`` (copia del submodule fatta
da ``scripts/apply_patches.sh multimark``), come nei worker; lo script si ferma se ``multimark``
viene da un'altra cartella. Nessun calcolo è reimplementato qui: per ogni campione
``multimark.inventory.inventory_code``, per ogni gruppo ``multimark.inventory.summarize`` e
``format_summary``.

Ingressi: i JSONL di ``scripts/export_inventory_inputs.py`` (bench-core), un file per gruppo, con
i campi ``sample_id``, ``code``, ``dataset``, ``problem_key``, ``language``, ``source``,
``model_id``, ``sample_index``, ``split``, ``loc``.

Uscite, in ``--out-dir``:

- ``<gruppo>.<variante>.csv``: per campione, i campi dell'ingresso (senza ``code``; ``loc``
  rinominato ``loc_bench``) seguiti dalle colonne di ``inventory_code`` (``parse_ok``, ``error``,
  ``n_sites_total``, ``n_sites_<canale>``, ``n_vars``, ``n_functions``, ``loc``, ...);
- ``--summary`` (testo, di solito ``<out-dir>/summary.<variante>.txt``) e ``--summary-json``:
  riepilogo di ogni gruppo, poi per dataset (se il gruppo ne ha più di uno) e, con
  ``--loc-bands``, per fascia di ``loc_bench``.

``<variante>`` riassume le opzioni (``canonical-kind_guards-on_params-off`` per la base di
multimark). Come le altre fasi, un file d'uscita che esiste già con lo stesso contenuto non si
riscrive; con contenuto diverso lo script si ferma senza toccarlo.

Uso (vedi scripts/inventory_sites.sh, che lancia esportazione e inventario in un job)::

    <posthoc>/bin/python scripts/inventory_sites.py --input DIR_O_FILE [...] --out-dir DIR \\
        --summary DIR/summary.canonical-kind_guards-on_params-off.txt [--include-params]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIBRARY = REPO_ROOT / "build" / "patched" / "multimark"
INPUT_FIELDS = (
    "sample_id",
    "dataset",
    "problem_key",
    "language",
    "source",
    "model_id",
    "sample_index",
    "split",
)
LOC_FIELD = "loc_bench"


class InventoryError(RuntimeError):
    """Ingresso non valido o uscita già presente con contenuto diverso."""


def import_library(library: Path) -> Any:
    """Importa ``multimark.inventory`` da ``library`` (prima di ogni altro percorso).

    Raises:
        InventoryError: se la cartella manca o il pacchetto importato viene da altrove.
    """
    library = library.resolve()
    if not (library / "multimark" / "__init__.py").is_file():
        raise InventoryError(
            f"{library}/multimark not found: run bash scripts/apply_patches.sh multimark"
        )
    sys.path.insert(0, str(library))
    import multimark
    from multimark import inventory

    origin = Path(multimark.__file__).resolve().parent
    if origin != library / "multimark":
        raise InventoryError(f"multimark imported from {origin}, expected {library / 'multimark'}")
    return inventory


def source_commit(library: Path) -> str:
    """Commit del submodule copiato (``build/patched/multimark.source_commit``)."""
    path = library.with_name(library.name + ".source_commit")
    return path.read_text(encoding="utf-8").strip() if path.is_file() else "unknown"


def variant_name(canonical: str, guards: bool, include_params: bool) -> str:
    """Nome della variante delle opzioni dell'inventario."""
    return (
        f"canonical-{canonical}_guards-{'on' if guards else 'off'}"
        f"_params-{'on' if include_params else 'off'}"
    )


def input_files(paths: Sequence[Path]) -> list[Path]:
    """File JSONL da file o cartelle (``*.jsonl`` in ordine di nome)."""
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(path.glob("*.jsonl")))
        elif path.is_file():
            out.append(path)
        else:
            raise InventoryError(f"input not found: {path}")
    if not out:
        raise InventoryError("no JSONL input")
    return out


def read_records(path: Path) -> list[dict[str, Any]]:
    """Righe del JSONL; ogni riga deve avere ``code`` (stringa) e i campi dell'esportazione."""
    records = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            obj = json.loads(line)
            missing = [k for k in (*INPUT_FIELDS, "code", "loc") if k not in obj]
            if missing or not isinstance(obj.get("code"), str):
                raise InventoryError(f"{path}:{lineno}: missing fields {missing or ['code']}")
            records.append(obj)
    return records


def inventory_rows(
    inventory: Any, records: Sequence[dict[str, Any]], options: dict[str, Any]
) -> list[dict[str, Any]]:
    """Righe per campione: campi dell'ingresso più ``inventory_code`` della libreria."""
    rows = []
    for rec in records:
        row: dict[str, Any] = {k: rec[k] for k in INPUT_FIELDS}
        row[LOC_FIELD] = rec["loc"]
        row.update(inventory.inventory_code(rec["code"], **options))
        rows.append(row)
    return rows


def parse_bands(text: str | None) -> list[tuple[int, int | None]]:
    """Fasce di righe da confini crescenti: ``"10,20"`` → [0, 10), [10, 20), [20, ∞)."""
    if not text:
        return []
    edges = [int(x) for x in text.split(",") if x.strip()]
    if any(edges[i + 1] <= edges[i] for i in range(len(edges) - 1)) or any(e <= 0 for e in edges):
        raise InventoryError(f"--loc-bands must be positive and increasing: {text}")
    lows = [0, *edges]
    highs: list[int | None] = [*edges, None]
    return [(lows[i], highs[i]) for i in range(len(lows))]


def band_label(low: int, high: int | None) -> str:
    return f"loc {low}+" if high is None else f"loc {low}-{high - 1}"


def strata(
    rows: Sequence[dict[str, Any]], bands: Sequence[tuple[int, int | None]]
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Sottoinsiemi riassunti oltre al gruppo intero: per dataset (se più di uno), per fascia."""
    out: list[tuple[str, list[dict[str, Any]]]] = []
    datasets = sorted({str(r["dataset"]) for r in rows})
    if len(datasets) > 1:
        out.extend((f"dataset={d}", [r for r in rows if r["dataset"] == d]) for d in datasets)
    for low, high in bands:
        sel = [r for r in rows if r[LOC_FIELD] >= low and (high is None or r[LOC_FIELD] < high)]
        out.append((band_label(low, high), sel))
    return out


def csv_bytes(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(
        buf, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def write_if_new(path: Path, data: bytes) -> str:
    """Scrittura atomica; ``"unchanged"`` se c'è già lo stesso contenuto.

    Raises:
        InventoryError: se il file esiste con contenuto diverso (non si sovrascrive).
    """
    if path.exists():
        if hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest():
            return "unchanged"
        raise InventoryError(f"{path} exists with different content: move it away and rerun")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return "written"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--input", nargs="+", required=True, type=Path, help="JSONL files or dirs")
    p.add_argument("--out-dir", required=True, type=Path, help="per-sample tables")
    p.add_argument("--summary", type=Path, help="text summary (default: stdout)")
    p.add_argument("--summary-json", type=Path, help="summary as JSON")
    p.add_argument("--library", type=Path, default=DEFAULT_LIBRARY, help="patched multimark copy")
    p.add_argument("--canonical", default="kind", help="canonical forms: kind (base) or paper")
    p.add_argument("--no-guards", action="store_true", help="drop semantic channel conditions")
    p.add_argument("--include-params", action="store_true", help="count parameters as names")
    p.add_argument("--loc-bands", help="comma-separated loc band edges (e.g. 10,20,40)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inventory = import_library(args.library)
        options = {
            "canonical": args.canonical,
            "guards": not args.no_guards,
            "include_params": args.include_params,
        }
        variant = variant_name(args.canonical, not args.no_guards, args.include_params)
        bands = parse_bands(args.loc_bands)
        columns = [*INPUT_FIELDS, LOC_FIELD, "parse_ok", "error", *inventory.STAT_COLUMNS]
        commit = source_commit(args.library.resolve())
        text = [
            f"multimark source commit: {commit}",
            f"options: {variant}" + (f" loc_bands={args.loc_bands}" if bands else ""),
            "",
        ]
        summary_json: dict[str, Any] = {"source_commit": commit, "options": options, "groups": {}}
        for path in input_files(args.input):
            records = read_records(path)
            rows = inventory_rows(inventory, records, options)
            out = args.out_dir / f"{path.stem}.{variant}.csv"
            outcome = write_if_new(out, csv_bytes(rows, columns))
            print(
                f"[{'SKIP' if outcome == 'unchanged' else 'OK'}] {path.stem}: "
                f"{len(rows)} rows -> {out}",
                file=sys.stderr,
                flush=True,
            )
            input_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            loc_mismatch = sum(1 for r in rows if r.get("parse_ok") and r["loc"] != r[LOC_FIELD])
            group: dict[str, Any] = {
                "input_sha256": input_sha,
                "loc_mismatch": loc_mismatch,
                "summary": inventory.summarize(rows),
                "strata": {},
            }
            text.append(inventory.format_summary(group["summary"], path.stem))
            text.append(f"input sha256: {input_sha}")
            text.append(f"rows with library loc != bench loc: {loc_mismatch}")
            text.append("")
            for label, sel in strata(rows, bands):
                summary = inventory.summarize(sel)
                group["strata"][label] = summary
                text.append(inventory.format_summary(summary, f"{path.stem} [{label}]"))
            summary_json["groups"][path.stem] = group
        report = "\n".join(text).rstrip("\n") + "\n"
        if args.summary:
            write_if_new(args.summary, report.encode("utf-8"))
        else:
            sys.stdout.write(report)
        if args.summary_json:
            data = json.dumps(summary_json, indent=2, sort_keys=True) + "\n"
            write_if_new(args.summary_json, data.encode("utf-8"))
    except InventoryError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
