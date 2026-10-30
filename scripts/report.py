"""Report della Milestone 14 dai soli artefatti (``bench.report``).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/report.py                     # scrive in $WMB/report/
    python scripts/report.py --repo-copy         # e copia tabelle e figure in report/ del repo

Rigenera tutto ogni volta (tabelle CSV e LaTeX, figure PDF e PNG, efficienza, README con le
esclusioni I6 e le deviazioni di configs/report/annotations.yaml); non scrive negli artefatti.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from bench.config.builder import config_dir, load_experiment
from bench.report import build_report
from bench.store.artifact_store import ArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=None, help="default: $WMB/report")
    parser.add_argument("--repo-copy", action="store_true")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=metrics"])
    out = Path(args.out) if args.out else Path(cfg.paths.wmb) / "report"
    written = build_report(ArtifactStore(Path(cfg.paths.artifacts)), cfg, out,
                           config_dir() / "report" / "annotations.yaml")  # fmt: skip
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
