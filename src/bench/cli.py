"""Entry point ``bench`` (SPEC §16.4).

- ``bench doctor [--gpu] [--json PATH] [override Hydra ...]``: diagnostica.
- ``bench stage=<fase> [override Hydra ...]``: esegue una fase tramite ``@hydra.main``.
- ``bench submit [--dry-run] stage=<fase> [override ...]``: un job SLURM per cella (ADR-006).

Il report del doctor e il riepilogo delle fasi si scrivono su stdout; tutto il resto
passa da ``logging``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import hydra
from omegaconf import DictConfig

from bench.config.builder import ExperimentBuilder, config_dir, load_experiment
from bench.domain.errors import BenchError

logger = logging.getLogger("bench.cli")


def _doctor(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="bench doctor", description="Check the installation.")
    parser.add_argument("--gpu", action="store_true", help="also run GPU checks (GPU node only)")
    parser.add_argument("--json", type=Path, help="also write the report as JSON to this file")
    parser.add_argument("overrides", nargs="*", help="Hydra overrides, e.g. paths=local")
    ns = parser.parse_args(list(args))

    from bench.pipeline.facade import BenchmarkFacade

    try:
        cfg = load_experiment([*ns.overrides, "stage=doctor"])
    except BenchError as exc:
        sys.stderr.write(f"bench doctor: {exc}\n")
        return 2
    report = BenchmarkFacade(cfg).doctor(gpu=ns.gpu)
    sys.stdout.write(report.render())
    if ns.json is not None:
        ns.json.parent.mkdir(parents=True, exist_ok=True)
        ns.json.write_text(report.to_json(), encoding="utf-8")
    return report.exit_code


def _submit(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bench submit", description="Submit one SLURM job per cell of a stage."
    )
    parser.add_argument("--dry-run", action="store_true", help="print the jobs without submitting")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="number of parallel SLURM jobs; each runs a disjoint set of cells in sequence",
    )
    parser.add_argument(
        "--share",
        default="1/1",
        help="take only share K of M (e.g. 1/2 and 2/2 to split the cells between two people)",
    )
    parser.add_argument(
        "--allow-concurrent",
        action="store_true",
        help="submit even if jobs of the same stage are already queued (may duplicate cells)",
    )
    parser.add_argument("overrides", nargs="*", help="Hydra overrides; stage=<name> is required")
    ns = parser.parse_args(list(args))

    from bench.doctor import load_cluster_profiles
    from bench.pipeline.submit import parse_share, submit

    try:
        share = parse_share(ns.share)
        cfg = load_experiment(ns.overrides)
        # Profili del codice in esecuzione (non di paths.repo, che indica il clone sul cluster).
        profiles = load_cluster_profiles(config_dir() / "cluster")
        jobs = submit(
            cfg,
            cfg.stage,
            profiles,
            dry_run=ns.dry_run,
            n_jobs=ns.jobs,
            allow_concurrent=ns.allow_concurrent,
            share=share,
        )
    except BenchError as exc:
        sys.stderr.write(f"bench submit: {exc}\n")
        return 2
    for job in jobs:
        sys.stdout.write(f"SLURM job {job.job_id} ({job.profile}), cells run in sequence:\n")
        sys.stdout.write(f"  parameters: {json.dumps(job.params, sort_keys=True)}\n")
        for i, cell in enumerate(job.cells, start=1):
            sys.stdout.write(f"  {i:3d}. {cell}\n")
    status = "planned (dry run)" if ns.dry_run else "submitted"
    n_cells = sum(len(j.cells) for j in jobs)
    sys.stdout.write(f"{len(jobs)} SLURM job(s) for {n_cells} cell(s) {status}\n")
    return 0


def _run_stage(cfg: DictConfig) -> None:
    from bench.pipeline.facade import BenchmarkFacade

    try:
        experiment = ExperimentBuilder.from_hydra(cfg).build()
        logging.getLogger("bench").setLevel(experiment.log_level)
        report = BenchmarkFacade(experiment).run_stage(experiment.stage)
    except BenchError as exc:
        logger.error("%s", exc)
        sys.stderr.write(f"bench: {exc}\n")
        raise SystemExit(1) from exc
    sys.stdout.write(report.summary() + "\n")


def _hydra(args: Sequence[str]) -> None:
    sys.argv = [sys.argv[0] if sys.argv else "bench", *args]
    entry = hydra.main(config_path=str(config_dir()), config_name="config", version_base="1.3")(
        _run_stage
    )
    entry()


def main(argv: Sequence[str] | None = None) -> int:
    """Punto d'ingresso del comando ``bench``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "doctor":
        return _doctor(args[1:])
    if args and args[0] == "submit":
        return _submit(args[1:])
    _hydra(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
