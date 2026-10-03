"""``bench submit``: le celle di una fase in UN solo job SLURM, eseguite in sequenza (ADR-006).

Il cluster universitario è condiviso e limita i job per utente (``AssocMaxSubmitJobLimit``):
per questo non si usa un job array ma un job che esegue le celle una dopo l'altra. Il job è
``Checkpointable``: al timeout submitit lo rimette in coda e la fase riparte (celle complete
saltate, cella in corso ripresa dal file parziale).

Il profilo (partizione, account, QoS, gres, CPU, memoria, timeout) viene dalla
``ResourcePolicy`` ed è già validato. Se le celle richiedono profili diversi (es. CPU e GPU)
si invia un job per profilo, incatenati con ``--dependency=afterany``.

Con ``--jobs N`` le celle si dividono in N job sequenziali con celle **disgiunte**, bilanciati
sul numero di problemi: N job girano in parallelo senza mai toccare la stessa cella. Un nuovo
invio è rifiutato se ci sono già job della fase in coda (eviterebbe celle duplicate).

SPEC §16.3 prevede ``hydra-submitit-launcher``: si usa submitit direttamente (la stessa
libreria) perché il profilo dipende dalla cella.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.config.resources import ResourceClass, ResourcePolicy
from bench.config.schema import ClusterProfile, ExperimentConfig
from bench.domain.errors import ConfigError
from bench.pipeline.stage import Cell, CellPlanner
from bench.registry import STAGES

logger = logging.getLogger(__name__)

# Rimesse in coda massime del job (le 16 celle di L1 non stanno in un solo job da 5,5 h).
MAX_REQUEUES = 30


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
    """Celle che condividono lo stesso profilo SLURM (un job sequenziale)."""

    resource: ResourceClass
    profile: ClusterProfile
    cells: list[Cell] = field(default_factory=list)


def plan_submission(
    cfg: ExperimentConfig, stage: str, profiles: dict[str, ClusterProfile]
) -> list[JobGroup]:
    """Celle della fase raggruppate per profilo, nell'ordine del ``CellPlanner``.

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
    cfg: ExperimentConfig, profile: ClusterProfile, stage: str, after: str | None = None
) -> dict[str, Any]:
    """Parametri di ``submitit.AutoExecutor.update_parameters`` per un profilo.

    Args:
        cfg: configurazione validata.
        profile: profilo SLURM.
        stage: nome della fase (per il nome del job).
        after: job da attendere prima di partire (``--dependency=afterany``).
    """
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
        "slurm_wckey": None,  # submitit aggiungerebbe --wckey=submitit
        "slurm_setup": [f"export {k}={v}" for k, v in sorted(job_environment(cfg).items())],
    }
    if profile.gres:
        params["slurm_gres"] = profile.gres
    if after:
        params["slurm_dependency"] = f"afterany:{after}"
    return params


def run_cell(cfg_data: dict[str, Any], stage: str, cell_data: dict[str, Any]) -> str:
    """Esegue la fase su una cella (con ripresa); restituisce il riepilogo."""
    from bench.config.builder import ExperimentBuilder
    from bench.pipeline.facade import BenchmarkFacade

    cfg = ExperimentBuilder.from_dict(cfg_data).build()
    report = BenchmarkFacade(cfg).run_stage(stage, cells=[Cell(**cell_data)])
    return report.summary()


class SequentialJob:
    """Corpo del job SLURM: le celle una dopo l'altra (``Checkpointable`` per submitit)."""

    def __call__(self, cfg_data: dict[str, Any], stage: str, cells: list[dict[str, Any]]) -> str:
        return "\n".join(run_cell(cfg_data, stage, cell) for cell in cells)

    def checkpoint(self, cfg_data: dict[str, Any], stage: str, cells: list[dict[str, Any]]) -> Any:
        """Stessa chiamata, rimessa in coda al segnale di timeout."""
        from submitit.helpers import DelayedSubmission

        return DelayedSubmission(self, cfg_data, stage, cells)


@dataclass(frozen=True)
class SubmittedJob:
    """Job SLURM inviato (o pianificato in dry-run) con le sue celle."""

    job_id: str
    profile: str
    cells: list[str]
    params: dict[str, Any]


def cell_weight(cfg: ExperimentConfig, cell: Cell) -> int:
    """Peso di una cella per il bilanciamento: problemi della parte (1 se non noto)."""
    if not (cell.level and cell.language and cell.split):
        return 1
    path = Path(cfg.paths.artifacts) / "data" / "problems" / f"{cell.level}_{cell.language}.parquet"
    if not path.is_file():
        return 1
    import pyarrow.parquet as pq

    splits = pq.read_table(path, columns=["split"]).column("split").to_pylist()
    return max(1, sum(1 for s in splits if s == cell.split))


def split_into_lanes(cells: list[Cell], weights: list[int], n_lanes: int) -> list[list[Cell]]:
    """Divide le celle in ``n_lanes`` gruppi disgiunti di peso simile (LPT).

    Le celle più pesanti vanno per prime nel gruppo più leggero; dentro ogni gruppo si
    mantiene l'ordine originale. Gruppi vuoti vengono scartati.
    """
    if n_lanes < 1:
        raise ConfigError("the number of jobs must be at least 1")
    lanes: list[list[int]] = [[] for _ in range(n_lanes)]
    loads = [0] * n_lanes
    for index in sorted(range(len(cells)), key=lambda i: (-weights[i], i)):
        lane = min(range(n_lanes), key=lambda k: (loads[k], k))
        lanes[lane].append(index)
        loads[lane] += weights[index]
    return [[cells[i] for i in sorted(lane)] for lane in lanes if lane]


def active_jobs(name: str) -> list[str]:
    """Job SLURM dell'utente con questo nome ancora in coda o in esecuzione.

    Senza ``squeue`` (es. in locale) restituisce una lista vuota.
    """
    import getpass
    import subprocess

    try:
        out = subprocess.run(
            ["squeue", "-h", "-u", getpass.getuser(), "-n", name, "-o", "%i %T"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def submit(
    cfg: ExperimentConfig,
    stage: str,
    profiles: dict[str, ClusterProfile],
    dry_run: bool = False,
    n_jobs: int = 1,
    allow_concurrent: bool = False,
) -> list[SubmittedJob]:
    """Invia ``n_jobs`` job sequenziali per profilo, con celle disgiunte.

    Ogni cella appartiene a un solo job, quindi due job non lavorano mai sulla stessa cella.
    Con più profili, i job di un profilo partono dopo quelli del precedente
    (``--dependency=afterany``).

    Raises:
        ConfigError: se ci sono già job della fase in coda (salvo ``allow_concurrent``):
            un secondo invio rilancerebbe le stesse celle in parallelo.

    Returns:
        I job inviati (``job_id="dry-run"`` in dry-run), con celle e parametri.
    """
    import bench.pipeline.stages  # noqa: F401  (popola STAGES)

    name = f"wmb-{stage}"
    if not dry_run and not allow_concurrent:
        running = active_jobs(name)
        if running:
            raise ConfigError(
                f"jobs named {name} are already queued or running ({', '.join(running)}): "
                "cancel them with scancel first (completed problems are kept and resumed)"
            )
    jobs: list[SubmittedJob] = []
    cfg_data = cfg.model_dump(mode="json")
    previous: list[str] = []
    for group in plan_submission(cfg, stage, profiles):
        weights = [cell_weight(cfg, c) for c in group.cells]
        after = ":".join(previous) or None
        current: list[str] = []
        for lane in split_into_lanes(group.cells, weights, n_jobs):
            params = executor_parameters(cfg, group.profile, stage, after=after)
            cells = [c.key() for c in lane]
            if dry_run:
                jobs.append(SubmittedJob("dry-run", group.profile.name, cells, params))
                continue
            import submitit

            folder = Path(cfg.paths.artifacts) / "_slurm" / stage / "%j"
            executor = submitit.AutoExecutor(
                folder=str(folder), cluster="slurm", slurm_max_num_timeout=MAX_REQUEUES
            )
            executor.update_parameters(**params)
            slurm_job = executor.submit(
                SequentialJob(), cfg_data, stage, [c.as_dict() for c in lane]
            )
            current.append(str(slurm_job.job_id))
            jobs.append(SubmittedJob(current[-1], group.profile.name, cells, params))
        previous = current
    logger.info(
        "stage %s: %d SLURM job(s), %d cell(s)", stage, len(jobs), sum(len(j.cells) for j in jobs)
    )
    return jobs
