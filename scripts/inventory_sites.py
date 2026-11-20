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
  riepilogo di ogni gruppo, poi per dataset (se il gruppo ne ha più di uno), per fascia di
  ``loc_bench`` (``LOC_BANDS``: 1–5, 6–10, 11–20, 21–40, 41+) e per fascia di ``n_sites_total``
  (``SITE_BANDS``, come PIPELINE §5.6: 1–7, 8–12, 13–19, 20–31, 32–55, 56+). I campioni fuori da
  ogni fascia (0 righe, 0 siti) si contano a parte.

``<variante>`` riassume le opzioni (``canonical-kind_guards-on_params-off`` per la base di
multimark; con ``--module-level on`` si aggiunge ``_modlevel-on``, con ``--single-letter``
diverso da ``exclude`` si aggiunge ``_singleletter-<valore>``, PIPELINE v1.2.6 §4.2 punto 9;
con ``--linking`` diverso da ``function_channel`` si aggiunge ``_linking-<valore>``, con
``--name-variant`` diverso da ``V2`` si aggiunge ``_names-<valore>``: sono le condizioni del
lettore che dipendono dal legame e dalla variante dei nomi, MD20 e MD23 della libreria, per
contare i siti di ``mcss`` con le regole della pipeline v1.3.0).
``--channels`` (``mcss8``: canali 1-8 di MCSS; ``extended``: canali 1-14 della base di multimark)
è obbligatorio, perché il default della libreria è cambiato in P1 (14 canali): non entra nel
nome della variante ma nel riepilogo, e ``scripts/inventory_sites.sh`` separa le uscite per commit
del submodule. Come le altre fasi, un file d'uscita che esiste già con lo stesso contenuto non si
riscrive; con contenuto diverso lo script si ferma senza toccarlo.

Uso (vedi scripts/inventory_sites.sh, che lancia esportazione e inventario in un job)::

    <posthoc>/bin/python scripts/inventory_sites.py --input DIR_O_FILE [...] --out-dir DIR \\
        --channels extended --summary DIR/summary.canonical-kind_guards-on_params-off.txt \
        [--include-params] [--module-level on]
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
# Campi facoltativi dell'ingresso, copiati nella tabella e riassunti a parte se presenti (``role``
# del pool CodeNet): i gruppi che non li hanno producono le stesse uscite di prima.
OPTIONAL_FIELDS = ("role",)
# Fasce chiuse [low, high] (decisione dell'utente del 13 novembre 2026); None = senza limite.
LOC_BANDS: tuple[tuple[int, int | None], ...] = ((1, 5), (6, 10), (11, 20), (21, 40), (41, None))
# Fasce di siti di PIPELINE_metodo.md §5.6 (confini 13, 20, 32 della tabella di §3.10.3).
SITE_BANDS: tuple[tuple[int, int | None], ...] = (
    (1, 7),
    (8, 12),
    (13, 19),
    (20, 31),
    (32, 55),
    (56, None),
)


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


CHANNEL_SETS = ("mcss8", "extended")
# Valori di ``names.single_letter`` (multimark ``names.SINGLE_LETTER_MODES``, PIPELINE v1.2.6
# §4.2 punto 9); ``exclude`` è il default della libreria e non entra nel nome della variante.
SINGLE_LETTER_MODES = ("exclude", "extend_except_loop", "extend_all")
# Legame dei bit e variante dei nomi (multimark ``linking.LINKING_MODES`` e
# ``names.NAME_VARIANTS``, libreria 4ea0d43, MD20 e MD23): servono solo alle condizioni del lettore
# che ne dipendono. I default sono quelli della libreria e non entrano nel nome della variante.
LINKING_MODES = ("function_channel", "function_single_run", "positional")
NAME_VARIANTS = ("V0", "V1", "V1b", "V2", "V2b")
DEFAULT_LINKING, DEFAULT_NAME_VARIANT = "function_channel", "V2"


def variant_name(
    canonical: str,
    guards: bool,
    include_params: bool,
    module_level: bool = False,
    single_letter: str = "exclude",
    linking: str = DEFAULT_LINKING,
    name_variant: str = DEFAULT_NAME_VARIANT,
    bits_per_name: int = 1,
    same_kind: str = "off",
    natural_forms: str = "off",
) -> str:
    """Nome della variante delle opzioni dell'inventario (``_modlevel-on``,
    ``_singleletter-<valore>``, ``_linking-<valore>``, ``_names-<valore>``, ``_bpn-<valore>``,
    ``_samekind-<valore>`` e ``_natural-<valore>`` solo se diversi dal default: con i default i
    nomi restano quelli di P0/P1)."""
    return (
        f"canonical-{canonical}_guards-{'on' if guards else 'off'}"
        f"_params-{'on' if include_params else 'off'}"
        + ("_modlevel-on" if module_level else "")
        + ("" if single_letter == "exclude" else f"_singleletter-{single_letter}")
        + ("" if linking == DEFAULT_LINKING else f"_linking-{linking}")
        + ("" if name_variant == DEFAULT_NAME_VARIANT else f"_names-{name_variant}")
        + ("" if bits_per_name == 1 else f"_bpn-{bits_per_name}")
        + ("" if same_kind == "off" else f"_samekind-{same_kind}")
        + ("" if natural_forms == "off" else f"_natural-{natural_forms}")
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
        row.update({k: rec[k] for k in OPTIONAL_FIELDS if k in rec})
        row[LOC_FIELD] = rec["loc"]
        row.update(inventory.inventory_code(rec["code"], **options))
        rows.append(row)
    return rows


def in_band(value: int, band: tuple[int, int | None]) -> bool:
    """``value`` nella fascia chiusa ``[low, high]`` (``high = None``: senza limite)."""
    low, high = band
    return value >= low and (high is None or value <= high)


def band_label(field: str, band: tuple[int, int | None]) -> str:
    low, high = band
    return f"{field} {low}+" if high is None else f"{field} {low}-{high}"


def strata(rows: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Sottoinsiemi riassunti oltre al gruppo intero: per dataset (se più di uno), per fascia di
    ``loc_bench`` (``LOC_BANDS``) e per fascia di ``n_sites_total`` (``SITE_BANDS``, solo i
    campioni analizzati). Fasce chiuse, decise dall'utente il 13 novembre 2026."""
    out: list[tuple[str, list[dict[str, Any]]]] = []
    datasets = sorted({str(r["dataset"]) for r in rows})
    if len(datasets) > 1:
        out.extend((f"dataset={d}", [r for r in rows if r["dataset"] == d]) for d in datasets)
    for field in OPTIONAL_FIELDS:
        values = sorted({str(r[field]) for r in rows if field in r})
        out.extend((f"{field}={v}", [r for r in rows if str(r.get(field)) == v]) for v in values)
    for band in LOC_BANDS:
        out.append((band_label("loc", band), [r for r in rows if in_band(r[LOC_FIELD], band)]))
    parsed = [r for r in rows if r.get("parse_ok")]
    for band in SITE_BANDS:
        sel = [r for r in parsed if in_band(r["n_sites_total"], band)]
        out.append((band_label("n_sites_total", band), sel))
    return out


def outside_bands(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """Campioni fuori da tutte le fasce: ``loc_bench`` = 0 e ``n_sites_total`` = 0 (analizzati).

    Non entrano in nessuna fascia ma si contano nel riepilogo (nessun campione sparisce in
    silenzio); i campioni non analizzabili sono già nel conteggio ``parse_errors``."""
    loc_out = sum(1 for r in rows if not any(in_band(r[LOC_FIELD], b) for b in LOC_BANDS))
    parsed = [r for r in rows if r.get("parse_ok")]
    sites_out = sum(
        1 for r in parsed if not any(in_band(r["n_sites_total"], b) for b in SITE_BANDS)
    )
    return loc_out, sites_out


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
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")  # un nome per processo
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
    p.add_argument(
        "--module-level",
        choices=("on", "off"),
        default="off",
        help="module-level variables as name sites (names.module_level)",
    )
    p.add_argument(
        "--channels",
        choices=CHANNEL_SETS,
        required=True,
        help="structural channels: mcss8 (1-8, mcss) or extended (1-14, multimark base)",
    )
    p.add_argument(
        "--single-letter",
        choices=SINGLE_LETTER_MODES,
        default="exclude",
        help="one-letter variables as name sites (names.single_letter, default: exclude)",
    )
    p.add_argument(
        "--linking",
        choices=LINKING_MODES,
        default=DEFAULT_LINKING,
        help="bit linking, for the reader conditions that depend on it (mcss: positional)",
    )
    p.add_argument(
        "--name-variant",
        choices=NAME_VARIANTS,
        default=DEFAULT_NAME_VARIANT,
        help="names channel variant, for the reader conditions that depend on it (mcss: V1)",
    )
    # [multimark v1.3.2/v1.3.9] opzioni dei giri di P2: la libreria le accetta da a4c9f7f
    # (bits_per_name, same_kind) e dal commit di MD29 (natural_forms)
    p.add_argument(
        "--bits-per-name",
        type=int,
        choices=(1, 2),
        default=1,
        help="names.bits_per_name: n_sites_total counts each name this many times (default: 1)",
    )
    p.add_argument(
        "--same-kind",
        choices=("off", "binding_order"),
        default="off",
        help="canonical.same_kind: comparisons between two names (default: off)",
    )
    p.add_argument(
        "--natural-forms",
        choices=("off", "const_left", "const_left_zero"),
        default="off",
        help="canonical.natural_forms: no site with a constant operand (default: off)",
    )
    return p


def library_options(args: argparse.Namespace) -> dict[str, Any]:
    """Argomenti di ``inventory_code``: le opzioni facoltative solo se diverse dal default."""
    options = {
        "canonical": args.canonical,
        "guards": not args.no_guards,
        "include_params": args.include_params,
        "module_level": args.module_level == "on",
        "channels": args.channels,
    }
    # Solo se diverso dal default: con ``exclude`` riepiloghi e chiamate restano quelli di
    # prima (stesse uscite, anche con una libreria senza l'opzione).
    if args.single_letter != "exclude":
        options["single_letter"] = args.single_letter
    # Come sopra: solo se diversi dal default (la libreria li accetta dal commit 4ea0d43).
    if args.linking != DEFAULT_LINKING:
        options["linking"] = args.linking
    if args.name_variant != DEFAULT_NAME_VARIANT:
        options["name_variant"] = args.name_variant
    # Come sopra: solo se diversi dal default (riepiloghi e chiamate di prima invariati).
    if args.bits_per_name != 1:
        options["bits_per_name"] = args.bits_per_name
    if args.same_kind != "off":
        options["same_kind"] = args.same_kind
    if args.natural_forms != "off":
        options["natural_forms"] = args.natural_forms
    return options


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inventory = import_library(args.library)
        options = library_options(args)
        variant = variant_name(
            args.canonical,
            not args.no_guards,
            args.include_params,
            args.module_level == "on",
            args.single_letter,
            args.linking,
            args.name_variant,
            args.bits_per_name,
            args.same_kind,
            args.natural_forms,
        )
        columns = [*INPUT_FIELDS, LOC_FIELD, "parse_ok", "error", *inventory.STAT_COLUMNS]
        commit = source_commit(args.library.resolve())
        text = [
            f"multimark source commit: {commit}",
            f"options: {variant}, channels={args.channels}"
            + ("" if args.single_letter == "exclude" else f", single_letter={args.single_letter}")
            + ("" if args.linking == DEFAULT_LINKING else f", linking={args.linking}")
            + (
                ""
                if args.name_variant == DEFAULT_NAME_VARIANT
                else f", name_variant={args.name_variant}"
            )
            + ("" if args.bits_per_name == 1 else f", bits_per_name={args.bits_per_name}")
            + ("" if args.same_kind == "off" else f", same_kind={args.same_kind}")
            + ("" if args.natural_forms == "off" else f", natural_forms={args.natural_forms}"),
            "loc bands: " + ", ".join(band_label("loc", b) for b in LOC_BANDS),
            "site bands: " + ", ".join(band_label("n_sites_total", b) for b in SITE_BANDS),
            "",
        ]
        summary_json: dict[str, Any] = {"source_commit": commit, "options": options, "groups": {}}
        for path in input_files(args.input):
            records = read_records(path)
            rows = inventory_rows(inventory, records, options)
            out = args.out_dir / f"{path.stem}.{variant}.csv"
            extra = [f for f in OPTIONAL_FIELDS if any(f in r for r in rows)]
            head = [*INPUT_FIELDS, *extra]
            outcome = write_if_new(out, csv_bytes(rows, [*head, *columns[len(INPUT_FIELDS) :]]))
            print(
                f"[{'SKIP' if outcome == 'unchanged' else 'OK'}] {path.stem}: "
                f"{len(rows)} rows -> {out}",
                file=sys.stderr,
                flush=True,
            )
            input_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            loc_mismatch = sum(1 for r in rows if r.get("parse_ok") and r["loc"] != r[LOC_FIELD])
            loc_out, sites_out = outside_bands(rows)
            group: dict[str, Any] = {
                "input_sha256": input_sha,
                "loc_mismatch": loc_mismatch,
                "outside_loc_bands": loc_out,
                "outside_site_bands": sites_out,
                "summary": inventory.summarize(rows),
                "strata": {},
            }
            text.append(inventory.format_summary(group["summary"], path.stem))
            text.append(f"input sha256: {input_sha}")
            text.append(f"rows with library loc != bench loc: {loc_mismatch}")
            text.append(f"rows outside loc bands (loc_bench = 0): {loc_out}")
            text.append(f"parsed rows outside site bands (n_sites_total = 0): {sites_out}")
            text.append("")
            for label, sel in strata(rows):
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
