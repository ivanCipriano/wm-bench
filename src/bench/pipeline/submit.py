"""``bench submit``: le celle di una fase in UN solo job SLURM, eseguite in sequenza (ADR-006).

Il cluster universitario è condiviso e limita i job per utente (``AssocMaxSubmitJobLimit``):
per questo non si usa un job array ma un job che esegue le celle una dopo l'altra. Il job è
``Checkpointable``: al timeout submitit lo rimette in coda e la fase riparte (celle complete
saltate, cella in corso ripresa dal file parziale).

Il profilo (partizione, account, QoS, gres, CPU, memoria, timeout) viene dalla
``ResourcePolicy`` ed è già validato. Se le celle richiedono profili diversi (es. CPU e GPU)
si invia un job per profilo, incatenati con ``--dependency=afterany``.

Con ``--jobs N`` le celle si dividono in N job sequenziali con celle **disgiunte**, bilanciati
sul numero di problemi: N job girano in parallelo senza mai toccare la stessa cella. Con
``--share K/M`` più persone si spartiscono le celle (quote disgiunte e deterministiche). Un
nuovo invio è rifiutato se la stessa quota ha già job in coda nell'account di progetto.

SPEC §16.3 prevede ``hydra-submitit-launcher``: si usa submitit direttamente (la stessa
libreria) perché il profilo dipende dalla cella.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
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
_SQUEUE_FIELDS = 4
EXCLUDE_ENV = "WMB_EXCLUDE_NODES"  # nodi SLURM da escludere (--exclude), opzionale


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
    cfg: ExperimentConfig,
    profile: ClusterProfile,
    stage: str,
    after: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Parametri di ``submitit.AutoExecutor.update_parameters`` per un profilo.

    Args:
        cfg: configurazione validata.
        profile: profilo SLURM.
        stage: nome della fase (per il nome del job).
        after: job da attendere prima di partire (``--dependency=afterany``).
        name: nome del job (default ``wmb-<fase>``).
    """
    if profile.kind != "slurm" or profile.timeout_min is None:
        raise ConfigError(f"profile '{profile.name}' is not a SLURM profile")
    params: dict[str, Any] = {
        "name": name or job_name(stage),
        "timeout_min": profile.timeout_min,
        "cpus_per_task": profile.cpus_per_task,
        "mem_gb": profile.mem_gb,
        "slurm_partition": profile.partition,
        "slurm_account": profile.account,
        "slurm_qos": profile.qos,
        "slurm_wckey": None,  # submitit aggiungerebbe --wckey=submitit
        # --requeue: sul cluster JobRequeue = 0, quindi senza questa opzione `scontrol requeue`
        # (usato da submitit al timeout) è rifiutato e il job finisce in TIMEOUT senza ripartire.
        "slurm_additional_parameters": {"requeue": True},
        # umask 002: i file restano scrivibili dal gruppo (due utenti condividono gli artefatti).
        # Poi i comandi del profilo (es. module load apptainer per i job CPU).
        "slurm_setup": [
            "umask 002",
            *profile.setup,
            *(f"export {k}={v}" for k, v in sorted(job_environment(cfg).items())),
        ],
    }
    if profile.gres:
        params["slurm_gres"] = profile.gres
    if after:
        params["slurm_dependency"] = f"afterany:{after}"
    # Nodi da evitare (es. un nodo GPU guasto: "No CUDA GPUs are available"), lista SLURM
    # come ``gnode13`` o ``gnode[12-13]``.
    exclude = os.environ.get(EXCLUDE_ENV, "").strip()
    if exclude:
        params["slurm_additional_parameters"]["exclude"] = exclude
    return params


def run_cell(cfg_data: dict[str, Any], stage: str, cell_data: dict[str, Any]) -> str:
    """Esegue la fase su una cella (con ripresa); restituisce il riepilogo."""
    from bench.config.builder import ExperimentBuilder
    from bench.pipeline.facade import BenchmarkFacade

    cfg = ExperimentBuilder.from_dict(cfg_data).build()
    report = BenchmarkFacade(cfg).run_stage(stage, cells=[Cell(**cell_data)])
    return report.summary()


class SequentialJob:
    """Corpo del job SLURM: le celle una dopo l'altra (``Checkpointable`` per submitit).

    Una cella che fallisce non ferma le successive: l'errore viene registrato e, a fine job,
    si solleva un'eccezione con l'elenco delle celle fallite (il job risulta FAILED).
    """

    def __call__(self, cfg_data: dict[str, Any], stage: str, cells: list[dict[str, Any]]) -> str:
        summaries: list[str] = []
        failures: list[str] = []
        for cell in cells:
            try:
                summaries.append(run_cell(cfg_data, stage, cell))
            except Exception as exc:  # una cella non deve bloccare le altre
                key = Cell(**cell).key()
                logger.exception("cell %s failed; continuing with the next cells", key)
                failures.append(f"{key}: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError(
                f"{len(failures)} of {len(cells)} cell(s) failed:\n" + "\n".join(failures)
            )
        return "\n".join(summaries)

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
    """Peso di una cella per il bilanciamento: campioni da trattare (1 se non noto).

    Problemi della parte (tutti, se la cella non ha parte) per i campioni per problema:
    uno per le soluzioni canoniche, N (decoding del livello) altrimenti. Un fattore comune a
    tutte le celle non cambia la divisione.
    """
    if not (cell.level and cell.language):
        return 1
    path = Path(cfg.paths.artifacts) / "data" / "problems" / f"{cell.level}_{cell.language}.parquet"
    if not path.is_file():
        return 1
    import pyarrow.parquet as pq

    splits = pq.read_table(path, columns=["split"]).column("split").to_pylist()
    n_problems = sum(1 for s in splits if cell.split is None or s == cell.split)
    per_problem = 1 if cell.source == "canonical" else _samples_per_problem(cfg, cell.level)
    return max(1, n_problems * per_problem)


def _samples_per_problem(cfg: ExperimentConfig, level: str) -> int:
    key = "level1" if level == "L1" else "level234"
    decoding = cfg.decoding.get(key)
    return decoding.n if decoding is not None else 1


def split_into_lanes(
    cells: list[Cell], weights: list[int], n_lanes: int, keep_empty: bool = False
) -> list[list[Cell]]:
    """Divide le celle in ``n_lanes`` gruppi disgiunti di peso simile (LPT).

    Le celle più pesanti vanno per prime nel gruppo più leggero; dentro ogni gruppo si
    mantiene l'ordine originale. Il risultato è deterministico. I gruppi vuoti vengono
    scartati, salvo ``keep_empty`` (serve a indicizzare le quote in modo stabile).
    """
    if n_lanes < 1:
        raise ConfigError("the number of jobs must be at least 1")
    lanes: list[list[int]] = [[] for _ in range(n_lanes)]
    loads = [0] * n_lanes
    for index in sorted(range(len(cells)), key=lambda i: (-weights[i], i)):
        lane = min(range(n_lanes), key=lambda k: (loads[k], k))
        lanes[lane].append(index)
        loads[lane] += weights[index]
    return [[cells[i] for i in sorted(lane)] for lane in lanes if lane or keep_empty]


def parse_share(text: str) -> tuple[int, int]:
    """Quota ``"K/M"`` (la K-esima di M parti) -> ``(K, M)``.

    Raises:
        ConfigError: se il formato non è valido.
    """
    head, sep, tail = text.partition("/")
    if not sep or not head.isdigit() or not tail.isdigit() or not 1 <= int(head) <= int(tail):
        raise ConfigError(f"invalid share {text!r}: expected K/M with 1 <= K <= M (e.g. 1/2)")
    return int(head), int(tail)


def parse_shares(text: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """Quota ``"K/M"`` o quota divisa ``"K/M:k/m"`` -> ``((K, M), (k, m))``.

    ``"1/2:2/2"`` è la seconda metà della quota ``1/2``: serve a spartire fra due persone il
    lavoro rimasto in una quota già assegnata, senza toccare l'altra.

    Raises:
        ConfigError: se il formato non è valido o si divide la quota ``1/1``.
    """
    head, sep, tail = text.partition(":")
    share = parse_share(head)
    sub = parse_share(tail) if sep else (1, 1)
    if sub[1] > 1 and share[1] == 1:
        raise ConfigError(f"invalid share {text!r}: split K/M with M > 1, or use K/M alone")
    return share, sub


def job_name(
    stage: str,
    share: tuple[int, int] = (1, 1),
    methods: Sequence[str] | None = None,
    sub: tuple[int, int] = (1, 1),
) -> str:
    """Nome dei job SLURM di una fase: ``wmb-<fase>[.<metodi>][-s<k>of<m>[p<k>of<m>]]``.

    I metodi (separati da ``+``) compaiono solo per le fasi con l'asse ``method``: job di metodi
    diversi lavorano su celle disgiunte e possono girare insieme (``conflicting_jobs``). La
    parte ``p<k>of<m>`` indica una parte della quota (``--share K/M:k/m``).
    """
    k, m = share
    tag = f".{'+'.join(sorted(methods))}" if methods else ""
    if m == 1:
        return f"wmb-{stage}{tag}"
    part = f"p{sub[0]}of{sub[1]}" if sub[1] > 1 else ""
    return f"wmb-{stage}{tag}-s{k}of{m}{part}"


def stage_methods(
    cfg: ExperimentConfig, stage: str, cells: Sequence[Cell] | None = None
) -> list[str] | None:
    """Etichette del nome del job se la fase ha l'asse ``method``, altrimenti ``None``.

    Con le celle pianificate l'etichetta è il metodo di ogni cella o, per le celle senza metodo
    (es. ``canonical`` e ``llm_baseline`` di ``execute``), la sua sorgente: così l'esecuzione
    delle canoniche o della baseline non risulta in conflitto con i job di un metodo, con cui
    non condivide celle. Per i livelli diversi da L1 l'etichetta ha il livello in coda
    (``sweet_L2``): i job di L2 non vanno in conflitto con quelli dell'HPO di L1 dello stesso
    metodo, e i nomi dei job di L1 restano quelli della M7 e della M8. Le celle della parte di
    test aggiungono ``_test`` (``stone_test``, M10): non bloccano i job di sviluppo. Senza celle si
    usano i metodi della configurazione.
    """
    import bench.pipeline.stages  # noqa: F401  (popola STAGES)

    if "method" not in STAGES.get(stage).cell_axes:
        return None
    tags = set()
    for c in cells or ():
        tag = c.method or c.source
        if tag is not None:
            suffix = [c.level] if c.level and c.level != "L1" else []
            suffix += ["test"] if c.split == "test" else []
            tags.add("_".join([str(tag), *map(str, suffix)]))
    return sorted(tags) if tags else list(cfg.methods)


def queued_jobs(account: str) -> list[tuple[str, str, str, str]]:
    """Job in coda o in esecuzione di **tutti** gli utenti dell'account: (id, utente, stato, nome).

    Senza ``squeue`` (es. in locale) restituisce una lista vuota.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["squeue", "-h", "-A", account, "-o", "%i|%u|%T|%j"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == _SQUEUE_FIELDS:
            rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


def conflicting_jobs(
    stage: str,
    share: tuple[int, int],
    account: str,
    methods: Sequence[str] | None = None,
    sub: tuple[int, int] = (1, 1),
) -> list[str]:
    """Job della stessa fase che potrebbero lavorare sulle stesse celle.

    Sono in conflitto: il job senza quota (tutte le celle), la stessa quota, e qualunque quota
    di una divisione diversa (es. ``1/3`` contro ``1/2``). Quote diverse della stessa
    divisione (``1/2`` e ``2/2``) sono disgiunte e possono girare insieme. Così pure job con
    metodi disgiunti (``wmb-detect.sweet`` e ``wmb-detect.stone``); un job senza metodi nel nome
    conta come se li avesse tutti. Dentro la stessa quota, parti diverse della stessa divisione
    (``1/2:1/2`` e ``1/2:2/2``) sono disgiunte; la quota intera è in conflitto con ogni sua parte.
    """
    k, m = share
    base = f"wmb-{stage}"
    pattern = re.compile(
        rf"^{re.escape(base)}(?:\.([A-Za-z0-9_+]+))?(?:-s(\d+)of(\d+)(?:p(\d+)of(\d+))?)?$"
    )
    mine = set(methods or ())
    conflicts = []
    for job_id, user, state, name in queued_jobs(account):
        match = pattern.match(name)
        if match is None:
            continue
        theirs = set(match.group(1).split("+")) if match.group(1) else set()
        if mine and theirs and not mine & theirs:
            continue  # metodi diversi: celle disgiunte
        other = (int(match.group(2)), int(match.group(3))) if match.group(2) else (1, 1)
        other_sub = (int(match.group(4)), int(match.group(5))) if match.group(4) else (1, 1)
        if other != share and other[1] == m and m != 1:
            continue  # quote diverse della stessa divisione: celle disgiunte
        if other == share and other_sub != sub and other_sub[1] == sub[1] and sub[1] != 1:
            continue  # parti diverse della stessa quota: celle disgiunte
        conflicts.append(f"{job_id} {name} ({user}, {state})")
    return conflicts


def submit(
    cfg: ExperimentConfig,
    stage: str,
    profiles: dict[str, ClusterProfile],
    dry_run: bool = False,
    n_jobs: int = 1,
    allow_concurrent: bool = False,
    share: tuple[int, int] = (1, 1),
    sub: tuple[int, int] = (1, 1),
) -> list[SubmittedJob]:
    """Invia ``n_jobs`` job sequenziali per profilo, con celle disgiunte.

    Ogni cella appartiene a un solo job, quindi due job non lavorano mai sulla stessa cella.
    Con ``share=(K, M)`` le celle si dividono prima in M quote bilanciate e deterministiche
    (uguali per chiunque lanci lo stesso comando) e si invia solo la quota K: due persone
    possono così spartirsi il lavoro con ``--share 1/2`` e ``--share 2/2``. Con ``sub=(k, m)``
    la quota si divide a sua volta in m parti (stesso criterio) e si invia solo la parte k.
    Con più profili, i job di un profilo partono dopo quelli del precedente
    (``--dependency=afterany``).

    Raises:
        ConfigError: se ci sono già job della fase in coda (salvo ``allow_concurrent``):
            un secondo invio rilancerebbe le stesse celle in parallelo.

    Returns:
        I job inviati (``job_id="dry-run"`` in dry-run), con celle e parametri.
    """
    import bench.pipeline.stages  # noqa: F401  (popola STAGES)

    k, m = share
    groups = list(plan_submission(cfg, stage, profiles))
    methods = stage_methods(cfg, stage, [c for g in groups for c in g.cells])
    name = job_name(stage, share, methods, sub)
    if not dry_run and not allow_concurrent:
        running = conflicting_jobs(stage, share, cfg.slurm.account, methods, sub)
        if running:
            raise ConfigError(
                f"jobs that may run the same cells are already queued or running: "
                f"{'; '.join(running)}. Cancel them with scancel first (completed problems "
                "are kept and resumed)"
            )
    jobs: list[SubmittedJob] = []
    cfg_data = cfg.model_dump(mode="json")
    previous: list[str] = []
    for group in groups:
        all_weights = [cell_weight(cfg, c) for c in group.cells]
        mine = split_into_lanes(group.cells, all_weights, m, keep_empty=True)[k - 1]
        if sub[1] > 1:
            sub_weights = [all_weights[group.cells.index(c)] for c in mine]
            mine = split_into_lanes(mine, sub_weights, sub[1], keep_empty=True)[sub[0] - 1]
        weights = [all_weights[group.cells.index(c)] for c in mine]
        after = ":".join(previous) or None
        current: list[str] = []
        for lane in split_into_lanes(mine, weights, n_jobs):
            params = executor_parameters(cfg, group.profile, stage, after=after, name=name)
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
