"""``bench submit``: una cella = un job SLURM, inviati come job array con submitit (ADR-006).

Il profilo (partizione, account, QoS, gres, CPU, memoria, timeout) viene dalla
``ResourcePolicy`` ed è già validato (nessun job GPU su defq, timeout sotto i limiti).
SPEC §16.3 prevede ``hydra-submitit-launcher``: si usa submitit direttamente, la stessa
libreria su cui si basa il launcher, perché quest'ultimo applica un solo profilo a tutto il
multirun e non permette un profilo per cella.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.config.resources import ResourceClass, ResourcePolicy
from bench.config.schema import ClusterProfile, ExperimentConfig
from bench.domain.errors import ConfigError
from bench.pipeline.stage import Cell, CellPlanner
from bench.registry import STAGES

logger = logging.getLogger(__name__)


def job_environment(cfg: ExperimentConfig) -> dict[str, str]:
    """Variabili d'ambiente dei job (SPEC §8.1, cluster_info §2, §6)."""
    hub = cfg.paths.hf_hub_cache
    return {
        "HF_HOME": str(hub.parent),
        "HF_HUB_CACHE": str(hub),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONHASHSEED": "0",
    }


@dataclass
class JobGroup:
    """Celle che condividono lo stesso profilo SLURM (un job array)."""

    resource: ResourceClass
    profile: ClusterProfile
    cells: list[Cell] = field(default_factory=list)


def plan_submission(
    cfg: ExperimentConfig, stage: str, profiles: dict[str, ClusterProfile]
) -> list[JobGroup]:
    """Celle della fase raggruppate per profilo.

    Raises:
        ConfigError: se la fase è ignota o un profilo non è valido.
    """
    stage_cls = STAGES.get(stage)
    policy = ResourcePolicy(cfg.resources, profiles, cfg.slurm)
    groups: dict[ResourceClass, JobGroup] = {}
    for cell in CellPlanner(cfg).cells_for(stage_cls):
        resource = policy.resource_for(stage, method=cell.method, attack=cell.attack_id)
        if resource not in groups:
            groups[resource] = JobGroup(resource, policy.profile_for(resource))
        groups[resource].cells.append(cell)
    return list(groups.values())


def executor_parameters(
    cfg: ExperimentConfig, profile: ClusterProfile, stage: str, n_jobs: int
) -> dict[str, Any]:
    """Parametri di ``submitit.AutoExecutor.update_parameters`` per un profilo."""
    if profile.kind != "slurm" or profile.timeout_min is None:
        raise ConfigError(f"profile '{profile.name}' is not a SLURM profile")
    params: dict[str, Any] = {
        "name": f"wmb-{stage}",
        "timeout_min": profile.timeout_min,
        "cpus_per_task": profile.cpus_per_task,
        "mem_gb": profile.mem_gb,
        "slurm_partition": profile.partition,
        "slurm_account": profile.account,
        "slurm_qos": profile.qos,
        "slurm_array_parallelism": n_jobs,
        "slurm_wckey": None,  # submitit aggiungerebbe --wckey=submitit
        "slurm_setup": [f"export {k}={v}" for k, v in sorted(job_environment(cfg).items())],
    }
    if profile.gres:
        params["slurm_gres"] = profile.gres
    return params


def run_cell(cfg_data: dict[str, Any], stage: str, cell_data: dict[str, Any]) -> str:
    """Corpo del job SLURM: esegue la fase su una cella (con ripresa)."""
    from bench.config.builder import ExperimentBuilder
    from bench.pipeline.facade import BenchmarkFacade

    cfg = ExperimentBuilder.from_dict(cfg_data).build()
    report = BenchmarkFacade(cfg).run_stage(stage, cells=[Cell(**cell_data)])
    return report.summary()


class CellJob:
    """Job di una cella; al timeout SLURM submitit lo rimette in coda (``Checkpointable``).

    La ripresa vera e propria è nella fase (file parziale): il job rimesso in coda salta le
    parti già completate.
    """

    def __call__(self, cfg_data: dict[str, Any], stage: str, cell_data: dict[str, Any]) -> str:
        return run_cell(cfg_data, stage, cell_data)

    def checkpoint(self, cfg_data: dict[str, Any], stage: str, cell_data: dict[str, Any]) -> Any:
        """Stessa chiamata, rimessa in coda (usato da submitit al segnale di timeout)."""
        from submitit.helpers import DelayedSubmission

        return DelayedSubmission(self, cfg_data, stage, cell_data)


# Rimesse in coda massime del job sequenziale (16 celle L1 non stanno in 5,5 h).
MAX_REQUEUES = 30


class SequentialJob:
    """Un solo job SLURM che esegue le celle una dopo l'altra (``Checkpointable``)."""

    def __call__(self, cfg_data: dict[str, Any], stage: str, cells: list[dict[str, Any]]) -> str:
        return "\n".join(run_cell(cfg_data, stage, cell) for cell in cells)

    def checkpoint(self, cfg_data: dict[str, Any], stage: str, cells: list[dict[str, Any]]) -> Any:
        """Stessa chiamata, rimessa in coda al segnale di timeout."""
        from submitit.helpers import DelayedSubmission

        return DelayedSubmission(self, cfg_data, stage, cells)


@dataclass(frozen=True)
class SubmittedJob:
    """Job inviato (o pianificato in dry-run)."""

    job_id: str
    cell: str
    profile: str


def submit(
    cfg: ExperimentConfig,
    stage: str,
    profiles: dict[str, ClusterProfile],
    dry_run: bool = False,
) -> tuple[list[SubmittedJob], list[dict[str, Any]]]:
    """Invia un job per cella; con ``dry_run`` restituisce solo i parametri.

    Returns:
        I job (``job_id="dry-run"`` in dry-run) e i parametri di ciascun job array.
    """
    import bench.pipeline.stages  # noqa: F401  (popola STAGES)

    jobs: list[SubmittedJob] = []
    params_out: list[dict[str, Any]] = []
    cfg_data = cfg.model_dump(mode="json")
    for group in plan_submission(cfg, stage, profiles):
        params = executor_parameters(cfg, group.profile, stage, len(group.cells))
        params_out.append(params | {"cells": [c.key() for c in group.cells]})
        if dry_run:
            jobs += [SubmittedJob("dry-run", c.key(), group.profile.name) for c in group.cells]
            continue
        import submitit

        folder = Path(cfg.paths.artifacts) / "_slurm" / stage / "%j"
        # Cluster condiviso con limite di job per utente: UN solo job che esegue le celle in
        # sequenza. Al timeout viene rimesso in coda e riprende (celle complete saltate, cella
        # in corso ripresa dal file parziale).
        executor = submitit.AutoExecutor(
            folder=str(folder), cluster="slurm", slurm_max_num_timeout=MAX_REQUEUES
        )
        params.pop("slurm_array_parallelism", None)
        executor.update_parameters(**params)
        job = executor.submit(SequentialJob(), cfg_data, stage, [c.as_dict() for c in group.cells])
        jobs += [SubmittedJob(str(job.job_id), c.key(), group.profile.name) for c in group.cells]
    by_profile: dict[str, int] = defaultdict(int)
    for job in jobs:
        by_profile[job.profile] += 1
    logger.info("submitted %d jobs for stage %s: %s", len(jobs), stage, dict(by_profile))
    return jobs, params_out
