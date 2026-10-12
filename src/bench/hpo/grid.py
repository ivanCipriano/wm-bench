"""Griglie dell'HPO (SPEC §14.1) e risoluzione degli iperparametri di una cella.

Una griglia sta in ``configs/hpo/<metodo>.yaml``::

    method: stone
    space:                 # prodotto cartesiano (nomi del protocollo)
      gamma: [0.25, 0.5]
      delta: [0.5, 1.0]
    include:               # punti in più, es. il default del codice
      - {gamma: 0.5, delta: 1.0}
    replay:                # facoltativo (PromptMark, ADR-009)
      fixed: {iter_cap: 5, z_threshold: 1000000.0}

Con ``replay`` i punti di ``space`` non si generano: si ricavano da una **traiettoria** (il punto
con i valori di ``fixed``), una per combinazione degli altri iperparametri. I punti di ``include``
si generano sempre.

Ogni punto ha un ``config_hash`` (``MethodAdapter.config_hash``): le celle di uno sweep portano
l'hash nel campo ``Cell.config_hash`` e le fasi ne ricavano gli iperparametri con
:func:`resolve`. Una cella senza hash usa la configurazione di default del metodo (fino all'HPO).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from bench.config.builder import config_dir
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError

Role = Literal["candidate", "trajectory"]
PARTS = ("all", "trajectories", "candidates")


@dataclass(frozen=True)
class GridPoint:
    """Una configurazione della griglia.

    Attributes:
        hparams: iperparametri (nomi del protocollo).
        role: ``candidate`` (entra nella selezione) o ``trajectory`` (solo generazione, da cui
            si ricostruiscono i candidati, ADR-009).
        trajectory: per i candidati ricostruiti, gli iperparametri della traiettoria.
    """

    hparams: dict[str, Any]
    role: Role = "candidate"
    trajectory: dict[str, Any] | None = None


@dataclass(frozen=True)
class Grid:
    """Griglia di un metodo: candidati (default compreso) e traiettorie."""

    method: str
    points: list[GridPoint] = field(default_factory=list)

    def candidates(self) -> list[GridPoint]:
        return [p for p in self.points if p.role == "candidate"]

    def trajectories(self) -> list[GridPoint]:
        return [p for p in self.points if p.role == "trajectory"]

    def part(self, part: str) -> list[GridPoint]:
        """Punti di una parte dello sweep: ``all``, ``trajectories`` o ``candidates``."""
        if part not in PARTS:
            raise ConfigError(f"unknown hpo.part {part!r}; expected one of {PARTS}")
        if part == "trajectories":
            return self.trajectories()
        if part == "candidates":
            return self.candidates()
        return [*self.trajectories(), *self.candidates()]


def _product(space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not space:
        return []
    keys = list(space)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*space.values())]


def grid_path(method: str, root: Path | None = None) -> Path:
    return (root or config_dir() / "hpo") / f"{method}.yaml"


def load_grid(method: str, root: Path | None = None) -> Grid:
    """Griglia di ``configs/hpo/<metodo>.yaml``.

    Raises:
        ConfigError: file mancante o malformato, punti duplicati.
    """
    path = grid_path(method, root)
    if not path.is_file():
        raise ConfigError(f"no HPO grid for {method}: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("method") != method:
        raise ConfigError(f"{path}: method must be {method!r}, got {data.get('method')!r}")
    space = {str(k): list(v) for k, v in (data.get("space") or {}).items()}
    include = [dict(p) for p in (data.get("include") or [])]
    replay = data.get("replay")
    points: list[GridPoint] = []
    if replay:
        fixed = dict(replay.get("fixed") or {})
        if not fixed or not set(fixed) <= set(space):
            raise ConfigError(f"{path}: replay.fixed must name hyperparameters of space")
        free = {k: v for k, v in space.items() if k not in fixed}
        for base in _product(free) or [{}]:
            trajectory = {**base, **fixed}
            points.append(GridPoint(trajectory, role="trajectory"))
        for hp in _product(space):
            trajectory = {**hp, **fixed}
            points.append(GridPoint(hp, trajectory=trajectory))
    else:
        points.extend(GridPoint(hp) for hp in _product(space))
    points.extend(GridPoint(hp) for hp in include)
    keys = [repr(sorted(p.hparams.items())) + p.role for p in points]
    if len(set(keys)) != len(keys):
        raise ConfigError(f"{path}: duplicated grid points")
    return Grid(method, points)


def _grid_or_default(cfg: ExperimentConfig, adapter: Any) -> Grid:
    """La griglia del metodo se esiste, altrimenti il solo default (nessun HPO)."""
    method = str(adapter.name)
    if grid_path(method).is_file():
        return load_grid(method)
    return Grid(method, [GridPoint(dict(cfg.methods_catalog[method].default_hparams))])


def hashed_points(cfg: ExperimentConfig, adapter: Any) -> dict[str, GridPoint]:
    """``config_hash`` → punto, per tutti i punti della griglia (calcolati una volta)."""
    cached: dict[str, GridPoint] | None = getattr(adapter, "_grid_points", None)
    if cached is None:
        cached = {adapter.config_hash(p.hparams): p for p in _grid_or_default(cfg, adapter).points}
        adapter._grid_points = cached
    return cached


def resolve(
    cfg: ExperimentConfig, adapter: Any, config_hash: str | None
) -> tuple[dict[str, Any], str, GridPoint]:
    """Iperparametri, hash e punto della griglia di una cella.

    Senza hash: la configurazione di default del metodo.

    Raises:
        ConfigError: hash che non corrisponde né al default né a un punto della griglia.
    """
    default = adapter.default_hparams()
    default_hash = adapter.config_hash(default)
    if config_hash is None or config_hash == default_hash:
        return default, default_hash, GridPoint(default)
    point = hashed_points(cfg, adapter).get(config_hash)
    if point is None:
        raise ConfigError(f"{adapter.name}: config_hash {config_hash} is not in the HPO grid")
    return dict(point.hparams), config_hash, point


def sweep_hashes(cfg: ExperimentConfig, method: str, roles: tuple[Role, ...]) -> list[str]:
    """Hash delle configurazioni dello sweep di un metodo (``hpo.grid``, ``hpo.part``)."""
    from bench.pipeline.stages.watermark import make_adapter

    adapter = make_adapter(cfg, method)
    grid = load_grid(method)
    hashes = []
    for point in grid.part(cfg.hpo.part):
        if point.role in roles:
            h = adapter.config_hash(point.hparams)
            if h not in hashes:
                hashes.append(h)
    return hashes
