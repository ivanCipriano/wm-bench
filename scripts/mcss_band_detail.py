"""Dettaglio del profilo ``mcss_mod`` nella fascia 32-39 bit e sui file ammessi (PIPELINE §4.4.2).

Richiesta dell'utente del 15 novembre 2026, dai file già sul cluster (nessun nuovo inventario):
per i file ammessi del pool CodeNet (stessi filtri di ``select_mcss_selftest.eligible``: niente
``split == "test"``, niente problemi nello stesso cluster di un problema di test, niente errori
di parsing) e per quelli fra essi con ``n_bits_v1`` in 32-39, la media di ``n_bits_v1``, delle
variabili, dei siti strutturali, il rapporto strutturali/variabili (rapporto delle medie, accanto
a quello della Tabella 3 di MCSS, 10,9 / 11,9) e la media per canale dei siti strutturali.

Gira nell'ambiente **posthoc** (libreria standard e ``bench_contracts``, tramite
``select_mcss_selftest``). Il riepilogo va su stdout o in ``--report`` (che non deve esistere).

Uso::

    <posthoc>/bin/python scripts/mcss_band_detail.py --pool L2_codenet_pool.jsonl \\
        --mcss-table L2_codenet_pool.canonical-paper_guards-on_params-on_modlevel-on.csv \\
        [--test-clusters L2_codenet_pool.test_clusters.json] [--report REP.txt] \\
        [--single-letter exclude|extend_except_loop|extend_all] \\
        [--linking positional] [--name-variant V1]

``--single-letter``, ``--linking`` e ``--name-variant`` dicono come è stata letta la tabella (per
le tabelle della pipeline v1.3.0: ``extend_except_loop``, ``positional``, ``V1``) e finiscono solo
nell'intestazione.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import socket
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # select_mcss_selftest accanto
import select_mcss_selftest as sel

# Colonne di conteggio che non sono un canale strutturale.
NON_CHANNEL = frozenset({"n_sites_total", "n_sites_structural"})


def channel_columns(path: Path) -> list[str]:
    """Colonne ``n_sites_<canale>`` della tabella, nell'ordine del file."""
    with path.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    return [c for c in header if c.startswith("n_sites_") and c not in NON_CHANNEL]


def _mean(rows: Sequence[dict[str, str]], key: str) -> float:
    values = [int(r.get(key) or 0) for r in rows]
    return sum(values) / len(values) if values else float("nan")


def _fmt(value: float) -> str:
    return "n/a" if math.isnan(value) else f"{value:.2f}"


def _ratio(num: float, den: float) -> float:
    return num / den if den and not math.isnan(den) else float("nan")


def detail(
    pool: dict[str, dict[str, Any]],
    mcss: dict[str, dict[str, str]],
    cluster_pids: set[str],
    channels: Sequence[str],
    single_letter: str = "exclude",
    linking: str = "function_channel",
    name_variant: str = "V2",
) -> str:
    """Testo del riepilogo (vedi docstring del modulo); ``single_letter`` è la lettura di
    ``names.single_letter`` della tabella (PIPELINE v1.2.6 §4.2 punto 9), solo per
    l'intestazione."""
    ids, excluded = sel.eligible(pool, mcss, cluster_pids)
    band = sel.in_band(mcss, ids)
    sets = [(f"band {sel.BAND[0]}-{sel.BAND[1]}", band), ("eligible", ids)]
    width = 26
    head = f"{'':<{width}}" + "".join(f"{name:>14}" for name, _ in sets) + f"{'MCSS T3':>14}"
    lines = [
        "MCSS profile (mcss_mod: canonical paper, params on, module-level on, channels mcss8)"
        + sel.profile_suffix(single_letter, linking, name_variant),
        f"pool files: {len(pool)}; excluded split == test: {excluded['split_test']}, "
        f"same cluster as a test problem: {excluded['test_cluster']}, "
        f"parse errors: {excluded['parse_error']}",
        "",
        head,
        f"{'files':<{width}}" + "".join(f"{len(x):>14}" for _, x in sets) + f"{'':>14}",
        f"{'problems':<{width}}"
        + "".join(f"{len({sel._pid(pool[s]) for s in x}):>14}" for _, x in sets)
        + f"{'':>14}",
    ]
    rows = {name: [mcss[s] for s in x] for name, x in sets}
    paper = {
        "n_bits_v1": sel.PAPER_BITS,
        "n_vars": sel.PAPER_VARS,
        "n_sites_structural": sel.PAPER_STRUCTURAL,
    }
    for key in ("n_bits_v1", "n_vars", "n_vars_module", "n_sites_structural"):
        ref = f"{paper[key]:>14}" if key in paper else f"{'':>14}"
        lines.append(
            f"{'mean ' + key:<{width}}"
            + "".join(f"{_fmt(_mean(rows[n], key)):>14}" for n, _ in sets)
            + ref
        )
    lines.append(
        f"{'structural / variables':<{width}}"
        + "".join(
            f"{_fmt(_ratio(_mean(rows[n], 'n_sites_structural'), _mean(rows[n], 'n_vars'))):>14}"
            for n, _ in sets
        )
        + f"{sel.PAPER_STRUCTURAL / sel.PAPER_VARS:>14.2f}"
    )
    lines.append(f"  (ratio of the means; MCSS T3: {sel.PAPER_STRUCTURAL} / {sel.PAPER_VARS})")
    criterion = sel._criterion(_mean(rows[sets[0][0]], "n_bits_v1"))
    lines += ["", f"criterion on the band files (mean n_bits_v1): {criterion}", ""]
    lines.append("mean structural sites per channel:")
    lines.append(head.replace(f"{'MCSS T3':>14}", ""))
    empty = []
    for col in channels:
        if all(int(r.get(col) or 0) == 0 for n, _ in sets for r in rows[n]):
            empty.append(col[len("n_sites_") :])
            continue
        lines.append(
            f"{'  ' + col[len('n_sites_') :]:<{width}}"
            + "".join(f"{_fmt(_mean(rows[n], col)):>14}" for n, _ in sets)
        )
    if empty:
        lines.append("channels with no sites in either set: " + ", ".join(empty))
    return "\n".join(lines) + "\n"


def header(paths: Sequence[Path]) -> str:
    """Intestazione del log: data, nodo e SHA-256 degli ingressi."""
    lines = [
        "# mcss band detail (scripts/mcss_band_detail.py; PIPELINE v1.2 §4.4 point 2)",
        f"# date {datetime.now().astimezone().isoformat(timespec='seconds')}, "
        f"host {socket.gethostname()}",
    ]
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"# input {path} sha256 {digest}")
    return "\n".join(lines) + "\n\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--pool", required=True, type=Path, help="L2_codenet_pool[_extra].jsonl")
    p.add_argument("--mcss-table", required=True, type=Path, help="pool table, mcss_mod profile")
    p.add_argument("--test-clusters", type=Path, help="default: <pool stem>.test_clusters.json")
    p.add_argument("--report", type=Path, help="text report (default: stdout; must not exist)")
    p.add_argument(
        "--single-letter",
        choices=("exclude", "extend_except_loop", "extend_all"),
        default="exclude",
        help="names.single_letter reading of the table (header only)",
    )
    p.add_argument(
        "--linking",
        choices=("function_channel", "function_single_run", "positional"),
        default="function_channel",
        help="bit linking of the mcss table (header only; v1.3.0 tables: positional)",
    )
    p.add_argument(
        "--name-variant",
        choices=("V0", "V1", "V1b", "V2", "V2b"),
        default="V2",
        help="names variant of the mcss table (header only; v1.3.0 tables: V1)",
    )
    args = p.parse_args(argv)
    try:
        if args.report is not None and args.report.exists():
            raise sel.SelectionError(f"{args.report} already exists (logs are never overwritten)")
        pool = sel.read_pool(args.pool)
        mcss = sel.read_table(args.mcss_table)
        if set(mcss) != set(pool):
            raise sel.SelectionError("pool and table do not contain the same sample_id")
        clusters_path = args.test_clusters or sel.default_test_clusters(args.pool)
        clusters = sel.read_test_clusters(clusters_path)
        columns = channel_columns(args.mcss_table)
        text = detail(
            pool, mcss, clusters, columns, args.single_letter, args.linking, args.name_variant
        )
        text = header([args.pool, args.mcss_table, clusters_path]) + text
    except sel.SelectionError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if args.report is None:
        sys.stdout.write(text)
    else:
        sel.write_if_new(args.report, text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
