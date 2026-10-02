"""Fase astratta, cella di lavoro, contesto e pianificazione delle celle (SPEC §7.10)."""

from __future__ import annotations

import itertools
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import ClassVar

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.store.artifact_store import ArtifactStore
from bench.store.hashing import sha256_json
from bench.store.manifest import Manifest, ProvenanceCollector
from bench.store.refs import ArtifactRef

_UNSAFE = re.compile(r"[^A-Za-z0-9._=-]+")


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
        self._axes: dict[str, list[str]] = {
            "method": list(config.methods),
            "model_id": list(config.models),
            "language": [str(x) for x in config.languages],
            "level": [str(x) for x in config.levels],
            "split": [str(x) for x in config.splits],
            "attack_id": list(config.attacks),
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
        return [
            Cell(**dict(zip(stage.cell_axes, combo, strict=True)))
            for combo in itertools.product(*values)
        ]
