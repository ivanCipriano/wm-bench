"""Report della Milestone 14 dai soli artefatti (``bench.report``).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/report.py                     # scrive in $WMB/report/
    python scripts/report.py --repo-copy         # e copia tabelle e figure in report/ del repo
    python scripts/report.py --with-posthoc --out DIR   # P6: anche mcss e multimark

Rigenera tutto ogni volta (tabelle CSV e LaTeX, figure PDF e PNG, efficienza, README con le
esclusioni I6 e le deviazioni di configs/report/annotations.yaml); non scrive negli artefatti.

Con ``--with-posthoc`` (P6, ADR-019) il report comprende anche ``mcss`` e ``multimark``, con la
colonna ``label`` della configurazione e le tabelle degli attacchi su L2 e L3; richiede ``--out``
con una cartella che non esiste (nessun report già scritto si sovrascrive) e non copia nel
repository. Senza l'opzione il report dei cinque metodi è quello di prima.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from bench.config.builder import config_dir, load_experiment
from bench.report import METHODS, POSTHOC_METHODS, build_report
from bench.store.artifact_store import ArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=None, help="default: $WMB/report")
    parser.add_argument("--repo-copy", action="store_true")
    parser.add_argument("--with-posthoc", action="store_true",
                        help="also mcss and multimark (P6); needs a new --out folder")  # fmt: skip
    args = parser.parse_args()
    methods: tuple[str, ...] = METHODS
    if args.with_posthoc:
        if args.out is None or args.repo_copy:
            parser.error("--with-posthoc needs --out (a new folder) and no --repo-copy")
        if Path(args.out).exists():
            parser.error(f"{args.out} exists: the P6 report is never overwritten")
        methods = (*METHODS, *POSTHOC_METHODS)
    cfg = load_experiment(["paths=cluster", "stage=metrics"])
    out = Path(args.out) if args.out else Path(cfg.paths.wmb) / "report"
    written = build_report(ArtifactStore(Path(cfg.paths.artifacts)), cfg, out,
                           config_dir() / "report" / "annotations.yaml", methods)  # fmt: skip
    sys.stdout.write("\n".join(written) + f"\nreport in {out}\n")
    if args.repo_copy:
        target = Path(__file__).resolve().parent.parent / "report"
        for sub in ("tables", "figures"):
            if (out / sub).is_dir():
                shutil.copytree(out / sub, target / sub, dirs_exist_ok=True)
        shutil.copy2(out / "README.md", target / "README.md")
        sys.stdout.write(f"copied to {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
