"""Fase astratta, cella di lavoro, contesto e pianificazione delle celle (SPEC §7.10)."""

from __future__ import annotations

import functools
import itertools
import os
import re
import shutil
import socket
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any, ClassVar

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.store.artifact_store import ArtifactStore
from bench.store.hashing import sha256_json
from bench.store.manifest import Manifest, ProvenanceCollector
from bench.store.refs import ArtifactRef

_UNSAFE = re.compile(r"[^A-Za-z0-9._=-]+")


@functools.lru_cache(maxsize=1)
def hardware() -> dict[str, str | None]:
    """Nodo, partizione SLURM e GPU del processo (decisione dell'utente del 30 ottobre 2026: il
    tipo di GPU va nel manifest di ogni cella, perché A100 e H100 non danno numeri identici)."""
    gpu = None
    if os.environ.get("CUDA_VISIBLE_DEVICES") not in (None, "", "-1") and shutil.which(
        "nvidia-smi"
    ):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=20, check=False,
            )  # fmt: skip
            gpu = out.stdout.strip().splitlines()[0] if out.stdout.strip() else None
        except (OSError, subprocess.SubprocessError):
            gpu = None
    return {
        "node": socket.gethostname(),
        "slurm_partition": os.environ.get("SLURM_JOB_PARTITION"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "gpu": gpu,
    }


@dataclass(frozen=True)
class Cell:
    """Unità minima di lavoro; i campi assenti valgono ``None``."""

    method: str | None = None
    model_id: str | None = None
    language: str | None = None
    level: str | None = None
    split: str | None = None
    config_hash: str | None = None
    attack_id: str | None = None
    attack_params_hash: str | None = None
    source: str | None = None  # origine dei campioni (fase execute): canonical, llm_baseline

    def as_dict(self) -> dict[str, str | None]:
        """Campi della cella come dizionario."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def key(self) -> str:
        """Chiave canonica: solo i campi valorizzati, in ordine fisso; ``"all"`` se vuota."""
        parts = [f"{k}={v}" for k, v in self.as_dict().items() if v is not None]
        return "__".join(parts) if parts else "all"

    def slug(self) -> str:
        """Chiave utilizzabile come nome di file."""
        return _UNSAFE.sub("_", self.key())


class StageContext:
    """Dipendenze e metadati passati a ``Stage.run`` per una coppia (fase, cella).

    Args:
        config: configurazione validata.
        store: repository degli artefatti.
        provenance: raccoglitore della provenienza.
        stage_name: nome della fase in esecuzione.
        cell: cella in esecuzione.
    """

    def __init__(
        self,
        config: ExperimentConfig,
        store: ArtifactStore,
        provenance: ProvenanceCollector,
        stage_name: str,
        cell: Cell,
    ) -> None:
        self.config = config
        self.store = store
        self.provenance = provenance
        self.stage_name = stage_name
        self.cell = cell
        self.started_at = datetime.now(UTC)

    def make_manifest(
        self,
        ref: ArtifactRef,
        *,
        n_rows_in: int | None,
        n_rows_out: int | None,
        n_rows_expected: int | None,
        inputs: Sequence[ArtifactRef] = (),
        config_hash: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Manifest:
        """Prepara il manifest di un output; lo store aggiunge hash, dimensione e stato."""
        config_dump = self.config.model_dump(mode="json")
        input_hashes: dict[str, str] = {}
        for inp in inputs:
            manifest = self.store.read_manifest(inp)
            input_hashes[str(inp.path)] = (manifest.data_sha256 or "") if manifest else ""
        return Manifest(
            kind=ref.kind,
            stage=self.stage_name,
            cell=self.cell.as_dict(),
            config=config_dump,
            config_sha256=sha256_json(config_dump),
            config_hash=config_hash,
            extra={"hardware": hardware(), **(extra or {})},
            global_seed=self.config.global_seed,
            provenance=self.provenance.provenance,
            inputs=input_hashes,
            started_at=self.started_at,
            n_rows_in=n_rows_in,
            n_rows_out=n_rows_out,
            n_rows_expected=n_rows_expected,
        )


class Stage(ABC):
    """Fase della pipeline.

    Attributes:
        name: nome registrato della fase.
        resources: classe di risorse di default (la ``ResourcePolicy`` può raffinarla).
        output_kinds: tipi di artefatto prodotti, per risalire al produttore di un input.
        cell_axes: campi di ``Cell`` su cui la fase varia (prodotto cartesiano).
    """

    name: ClassVar[str]
    resources: ClassVar[ResourceClass]
    output_kinds: ClassVar[frozenset[str]]
    cell_axes: ClassVar[tuple[str, ...]] = ()
    # Sweep dell'HPO (``hpo.grid``): ruoli dei punti della griglia per cui la fase ha una cella
    # per configurazione (``Cell.config_hash``); vuoto = la fase non partecipa allo sweep.
    sweep_roles: ClassVar[tuple[str, ...]] = ()
    # Punti della griglia degli attacchi: tutti (``grid``) o solo quelli con il controllo negativo
    # sulla baseline (``negative_control``, D34).
    attack_points: ClassVar[str] = "grid"

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        """Istanzia la fase; le fasi che dipendono dalla configurazione lo ridefiniscono."""
        return cls()

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        """Adatta una cella del prodotto cartesiano (es. azzera gli assi che non servono).

        ``None`` scarta la cella; le celle uguali dopo la normalizzazione si contano una volta.
        """
        return cell

    @classmethod
    def sweep_cell(cls, cell: Cell) -> bool:
        """Se la cella (di un metodo) si espande per configurazione nello sweep."""
        return True

    @abstractmethod
    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        """Artefatti richiesti dalla fase per la cella."""

    @abstractmethod
    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        """Artefatti prodotti dalla fase per la cella."""

    @abstractmethod
    def run(self, cell: Cell, ctx: StageContext) -> None:
        """Esegue la fase: deve scrivere tutti gli ``outputs`` tramite ``ctx.store``."""


class CellPlanner:
    """Costruisce le celle di una fase dalle liste della configurazione.

    Args:
        config: configurazione validata.
    """

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config
        self._axes: dict[str, list[str]] = {
            "method": list(config.methods),
            "model_id": list(config.models),
            "language": [str(x) for x in config.languages],
            "level": [str(x) for x in config.levels],
            "split": [str(x) for x in config.splits],
            # Senza attacchi l'asse vale None: le fasi che lo hanno (execute, detect, metrics)
            # pianificano le celle senza attacco come prima della M10.
            "attack_id": list(config.attacks) or [None],  # type: ignore[list-item]
            "source": list(config.execution.sources),
        }

    def cells_for(self, stage: type[Stage]) -> list[Cell]:
        """Prodotto cartesiano degli assi della fase (una sola cella vuota se non ha assi).

        Raises:
            ConfigError: se un asse non è pianificabile dalla configurazione.
        """
        unknown = [a for a in stage.cell_axes if a not in self._axes]
        if unknown:
            raise ConfigError(f"stage '{stage.name}': cannot plan axes {unknown}")
        values = [self._axes[a] for a in stage.cell_axes]
        cells: list[Cell] = []
        for combo in itertools.product(*values):
            cell = stage.normalize_cell(Cell(**dict(zip(stage.cell_axes, combo, strict=True))))
            if cell is not None and cell not in cells:
                cells.append(cell)
        return self._selected(stage, self._attack_grid(stage, self._sweep(stage, cells)))

    @staticmethod
    def _attack_grid(stage: type[Stage], cells: list[Cell]) -> list[Cell]:
        """Una cella per combinazione dei parametri di ogni attacco (``configs/attack/*.yaml``):
        ``Cell.attack_params_hash``, come lo sweep dell'HPO per i metodi (ADR-012)."""
        if "attack_id" not in stage.cell_axes:
            return cells
        from bench.attacks.base import grid_attacks, negative_control_attacks

        control = stage.attack_points == "negative_control"
        points = negative_control_attacks if control else grid_attacks
        out: list[Cell] = []
        for cell in cells:
            if cell.attack_id is None or cell.attack_params_hash is not None:
                out.append(cell)
            else:
                found = points(cell.attack_id, language=cell.language)
                # niente celle per i linguaggi che l'attacco non supporta (T4: solo py e js)
                out.extend(
                    replace(cell, attack_params_hash=h)
                    for h, attack in found.items()
                    if cell.language is None or attack.supports(cell.language)
                )
        return out

    def _selected(self, stage: type[Stage], cells: list[Cell]) -> list[Cell]:
        """Sulla parte di test: la configurazione congelata del metodo (ADR-013).

        Con ``hpo.use_selected`` (e fuori da uno sweep) ogni cella con metodo, ``split=test`` e
        senza ``config_hash`` prende l'hash da ``freeze/<metodo>.json``; un metodo non congelato
        ferma la pianificazione. I linguaggi non applicabili restano senza hash.
        """
        cfg = self.config
        if not cfg.hpo.use_selected or cfg.hpo.grid is not None:
            return cells
        if not {"method", "split"} <= set(stage.cell_axes):
            return cells
        from bench.hpo.freeze import selected_hash

        store = ArtifactStore(cfg.paths.artifacts)
        out: list[Cell] = []
        for cell in cells:
            if cell.method and cell.split == "test" and cell.config_hash is None:
                h = selected_hash(store, cell.method, str(cell.model_id), str(cell.language))
                out.append(replace(cell, config_hash=h))
            else:
                out.append(cell)
        return out

    def _sweep(self, stage: type[Stage], cells: list[Cell]) -> list[Cell]:
        """Con ``hpo.grid``: una cella per configurazione della griglia, per le celle del metodo.

        Le celle senza metodo (es. esecuzione della baseline) restano come sono; quelle di altri
        metodi si scartano. Le fasi senza ``sweep_roles`` non si espandono. I punti di ``local``
        si espandono solo nelle loro celle (modello, linguaggio).
        """
        method = self.config.hpo.grid
        if method is None or not stage.sweep_roles:
            return cells
        from bench.hpo.grid import sweep_points

        points = sweep_points(self.config, method, stage.sweep_roles)  # type: ignore[arg-type]
        out: list[Cell] = []
        for cell in cells:
            if cell.method is None or not stage.sweep_cell(cell):
                out.append(cell)
            elif cell.method == method:
                out.extend(
                    replace(cell, config_hash=h)
                    for h, point in points
                    if point.applies_to(cell.model_id, cell.language)
                )
        return out
