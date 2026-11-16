"""Scelta di ``names.single_letter`` per ``mcss`` con i dati (PIPELINE v1.2.6 §4.2 punto 9).

Regola della pipeline (§4.2 punto 9): fra le letture ``exclude``, ``extend_except_loop`` ed
``extend_all``, per ``mcss`` si sceglie quella con il rapporto siti strutturali / variabili sui file
della fascia 32–39 bit più vicino a 0,92 (Tabella 3 di MCSS); a parità entro 0,05, quella con la
media di variabili più vicina a 11,9. Esito e numeri vanno poi in ``docs/audit/mcss.md``.

Per ogni lettura si usa la sua tabella ``mcss_mod`` del pool (``inventory_sites.py --channels mcss8
--canonical paper --include-params --module-level on [--single-letter ...]``); i file ammessi sono
quelli della selezione (``select_mcss_selftest.eligible``: niente ``split == "test"``, niente
problemi nello stesso cluster di un problema di test, niente errori di parsing) e la fascia è
``select_mcss_selftest.in_band`` sulla tabella della lettura (``n_bits_v1`` cambia con la lettura,
quindi cambiano anche i file della fascia). Il rapporto è quello delle medie, come in
``mcss_band_detail.py``. «A parità entro 0,05» si legge così: restano in gara le letture la cui
distanza |rapporto − 0,92| supera la minima di al più 0,05; fra queste vince la media di variabili
più vicina a 11,9 (a parità ulteriore, l'ordine delle letture qui sopra).

Gira nell'ambiente **posthoc** (libreria standard e ``bench_contracts``, tramite
``select_mcss_selftest``). Il riepilogo va su stdout o in ``--report`` (che non deve esistere).

Uso::

    <posthoc>/bin/python scripts/single_letter_choice.py --pool L2_codenet_pool_extra.jsonl \\
        --table exclude=T.csv --table extend_except_loop=T2.csv --table extend_all=T3.csv \\
        [--test-clusters POOL.test_clusters.json] [--report REP.txt]
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # select_mcss_selftest accanto
import mcss_band_detail as detail
import select_mcss_selftest as sel

MODES = ("exclude", "extend_except_loop", "extend_all")  # ordine di §4.2 punto 9
RATIO_TARGET = 0.92  # Tabella 3 di MCSS: 10,9 / 11,9, arrotondato come in §4.2 punto 9
VARS_TARGET = sel.PAPER_VARS  # 11,9
TIE = 0.05


def band_stats(
    pool: dict[str, dict[str, Any]],
    table: dict[str, dict[str, str]],
    cluster_pids: set[str],
) -> dict[str, Any]:
    """File, problemi e medie della fascia 32-39 della lettura (file ammessi della selezione)."""
    ids, _ = sel.eligible(pool, table, cluster_pids)
    band = sel.in_band(table, ids)
    rows = [table[s] for s in band]
    structural = detail._mean(rows, "n_sites_structural")
    variables = detail._mean(rows, "n_vars")
    return {
        "eligible": len(ids),
        "files": len(band),
        "problems": len({sel._pid(pool[s]) for s in band}),
        "n_bits_v1": detail._mean(rows, "n_bits_v1"),
        "n_vars": variables,
        "n_sites_structural": structural,
        "ratio": detail._ratio(structural, variables),
    }


def choose(stats: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, list[str]]:
    """``(lettura scelta, letture alla pari)`` secondo la regola (vedi docstring del modulo).

    Le letture senza file in fascia (rapporto non definito) non concorrono; se nessuna concorre,
    ``(None, [])``.
    """
    order = [m for m in MODES if m in stats] + sorted(set(stats) - set(MODES))
    valid = [m for m in order if not math.isnan(stats[m]["ratio"])]
    if not valid:
        return None, []
    dist = {m: abs(stats[m]["ratio"] - RATIO_TARGET) for m in valid}
    best = min(dist.values())
    tied = [m for m in valid if dist[m] - best <= TIE + 1e-12]
    chosen = min(tied, key=lambda m: (abs(stats[m]["n_vars"] - VARS_TARGET), order.index(m)))
    return chosen, tied


def report(stats: Mapping[str, Mapping[str, Any]]) -> str:
    """Testo del riepilogo: regola, numeri per lettura, esito."""
    chosen, tied = choose(stats)
    width = 28
    modes = list(stats)
    lines = [
        "Choice of names.single_letter for mcss (PIPELINE v1.2.6 §4.2 point 9)",
        "rule: the reading whose structural/variables ratio on the files of the 32-39 bit band",
        f"  is closest to {RATIO_TARGET} (MCSS Table 3); on a tie within {TIE}, the one whose mean",
        f"  number of variables is closest to {VARS_TARGET}",
        f"  (tie: |ratio - {RATIO_TARGET}| at most {TIE} above the smallest one; band and eligible",
        "  files as in the selection, band recomputed on each reading's mcss_mod table; ratio of",
        "  the means)",
        "",
        f"{'':<{width}}" + "".join(f"{m:>20}" for m in modes) + f"{'MCSS T3':>10}",
    ]
    for key, label in (("eligible", "eligible files"), ("files", "band files"),
                       ("problems", "band problems")):  # fmt: skip
        lines.append(f"{label:<{width}}" + "".join(f"{stats[m][key]:>20}" for m in modes))
    refs = {"n_bits_v1": sel.PAPER_BITS, "n_vars": VARS_TARGET,
            "n_sites_structural": sel.PAPER_STRUCTURAL, "ratio": RATIO_TARGET}  # fmt: skip
    for key in ("n_bits_v1", "n_vars", "n_sites_structural", "ratio"):
        label = "structural / variables" if key == "ratio" else f"mean {key}"
        lines.append(
            f"{label:<{width}}"
            + "".join(f"{detail._fmt(stats[m][key]):>20}" for m in modes)
            + f"{refs[key]:>10}"
        )
    lines.append(
        f"{'|ratio - ' + str(RATIO_TARGET) + '|':<{width}}"
        + "".join(f"{detail._fmt(abs(stats[m]['ratio'] - RATIO_TARGET)):>20}" for m in modes)
    )
    lines.append(
        f"{'|n_vars - ' + str(VARS_TARGET) + '|':<{width}}"
        + "".join(f"{detail._fmt(abs(stats[m]['n_vars'] - VARS_TARGET)):>20}" for m in modes)
    )
    lines.append("")
    if chosen is None:
        lines.append("outcome: undetermined (no reading has files in the band)")
    else:
        how = (
            "closest ratio"
            if len(tied) == 1
            else (f"tie within {TIE} among {', '.join(tied)}: closest mean of variables")
        )
        lines.append(f"outcome: names.single_letter = {chosen} for mcss ({how})")
        crit = sel._criterion(stats[chosen]["n_bits_v1"])
        lines.append(f"criterion of MCSS Table 3 on the band files of {chosen}: {crit}")
    return "\n".join(lines) + "\n"


def _table_arg(value: str) -> tuple[str, Path]:
    mode, sep, path = value.partition("=")
    if not sep or mode not in MODES or not path:
        raise argparse.ArgumentTypeError(f"expected MODE=PATH with MODE in {MODES}: {value!r}")
    return mode, Path(path)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--pool", required=True, type=Path, help="L2_codenet_pool[_extra].jsonl")
    p.add_argument("--table", required=True, action="append", type=_table_arg,
                   help="MODE=mcss_mod table of that reading (repeat)")  # fmt: skip
    p.add_argument("--test-clusters", type=Path, help="default: <pool stem>.test_clusters.json")
    p.add_argument("--report", type=Path, help="text report (default: stdout; must not exist)")
    args = p.parse_args(argv)
    tables = dict(args.table)
    try:
        if len(tables) != len(args.table):
            raise sel.SelectionError("each reading must be given once")
        if args.report is not None and args.report.exists():
            raise sel.SelectionError(f"{args.report} already exists (logs are never overwritten)")
        pool = sel.read_pool(args.pool)
        clusters_path = args.test_clusters or sel.default_test_clusters(args.pool)
        clusters = sel.read_test_clusters(clusters_path)
        stats = {}
        for mode in [m for m in MODES if m in tables]:
            table = sel.read_table(tables[mode])
            if set(table) != set(pool):
                raise sel.SelectionError(f"{tables[mode]}: not the same sample_id as the pool")
            stats[mode] = band_stats(pool, table, clusters)
        paths = [args.pool, clusters_path, *(tables[m] for m in stats)]
        text = detail.header(paths).replace(
            "# mcss band detail (scripts/mcss_band_detail.py; PIPELINE v1.2 §4.4 point 2)",
            "# single-letter choice for mcss (scripts/single_letter_choice.py)",
        ) + report(stats)
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
