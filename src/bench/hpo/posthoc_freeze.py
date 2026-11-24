"""Congelamento e controllo sul test dei metodi post-hoc multi-bit (P6, ADR-019).

Decisioni dell'utente del 24 novembre 2026 (PIPELINE di multimark v1.3.16, punti 2 e 3):

- **registro** ``configs/freeze/posthoc.yaml``: per ``mcss`` e ``multimark`` le configurazioni da
  congelare con un'etichetta (``main``; ``prudent`` e ``high_capacity``) e il commit della
  libreria. Il congelamento (fase ``freeze``) scrive in ``freeze/<metodo>.json`` una cella con
  etichetta per (modello, linguaggio, configurazione), senza passare da ``tune``;
- **controllo sul test, solo per questi metodi**: ogni pianificazione di celle ``split=test``
  rifiuta ``hpo.grid``, ``hpo.points``, ``hpo.use_selected=false``, un metodo non congelato, un
  ``config_hash`` che non è nel record, una copia patchata a un altro commit o con altre patch, un
  file di configurazione o una soglia della rilevazione aggregata con uno sha256 diverso da quello
  del record. Le celle senza ``config_hash`` si espandono nelle configurazioni congelate. Per gli
  altri metodi niente cambia (la falla di ``CellPlanner._selected`` con una griglia, che salta il
  congelamento, resta e si segnala in ``docs/deviations.md``);
- **fasi della seconda chiave** (``frozen_config``: ``detect_k2``, ``calibrate_k2``,
  ``metrics_remark``): le celle dei metodi del registro si espandono nelle configurazioni
  registrate anche su dev e prima del congelamento (k2 si calibra su dev prima del 1° dicembre,
  punto 6), poi in quelle del record.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import Field

from bench.config.builder import config_dir
from bench.domain.errors import ConfigError
from bench.domain.models import FrozenModel
from bench.hpo.freeze import FreezeRecord, file_sha256, load_freeze
from bench.store.artifact_store import ArtifactStore

if TYPE_CHECKING:
    from bench.config.schema import ExperimentConfig
    from bench.pipeline.stage import Cell, Stage

AGGREGATED_LEVELS = "L1L2"  # soglie della rilevazione aggregata: negativi di sviluppo L1+L2


class PosthocRegistry(FrozenModel):
    """Contenuto di ``configs/freeze/posthoc.yaml``."""

    library_commit: str
    methods: dict[str, dict[str, str]] = Field(default_factory=dict)

    def labels(self, method: str) -> dict[str, str]:
        """Etichetta -> hash delle configurazioni registrate di un metodo."""
        return dict(self.methods.get(method, {}))


def registry_path(root: Path | None = None) -> Path:
    return (root or config_dir()) / "freeze" / "posthoc.yaml"


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> PosthocRegistry:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    registry = PosthocRegistry.model_validate(data)
    for method, labels in registry.methods.items():
        hashes = list(labels.values())
        if len(set(hashes)) != len(hashes) or not hashes:
            raise ConfigError(f"{path}: {method} needs distinct config hashes")
    return registry


def load_registry(root: Path | None = None) -> PosthocRegistry:
    """Registro dei post-hoc (vuoto se il file non c'è).

    Raises:
        ConfigError: file malformato o hash ripetuti.
    """
    path = registry_path(root)
    if not path.is_file():
        return PosthocRegistry(library_commit="", methods={})
    try:
        return _load(str(path), path.stat().st_mtime)
    except (ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def is_registered(method: str | None) -> bool:
    return bool(method) and method in load_registry().methods


# --------------------------------------------------------------------------- sha256 dei file
def config_files(method: str, root: Path | None = None) -> list[str]:
    """File di configurazione congelati con il metodo (percorsi relativi a ``configs/``): metodo,
    griglia, registro e tutti gli attacchi."""
    base = root or config_dir()
    attacks = sorted(p.relative_to(base).as_posix() for p in (base / "attack").glob("*.yaml"))
    return [f"method/{method}.yaml", f"hpo/{method}.yaml", "freeze/posthoc.yaml", *attacks]


def config_files_sha256(method: str, root: Path | None = None) -> dict[str, str]:
    """sha256 dei file di :func:`config_files` (``missing`` se un file non c'è)."""
    base = root or config_dir()
    return {name: file_sha256(base / name) or "missing" for name in config_files(method, root)}


def patches_sha256(adapter: Any) -> str | None:
    """Contenuto di ``build/patched/<copia>.patches.sha256`` (patch applicate alla copia)."""
    path = Path(f"{adapter.source().root}.patches.sha256")
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def aggregated_path(method: str, model_id: str, cfg_hash: str, language: str = "python") -> str:
    """Soglie τ_k della rilevazione aggregata (§5.10), relative agli artefatti; le scrive
    ``scripts/report_aggregated.py calibrate``, il congelamento ne registra lo sha256."""
    return f"aggregated/{method}/{model_id}/{cfg_hash}/{AGGREGATED_LEVELS}_{language}.json"


# --------------------------------------------------------------------------- controllo sul test
def _record_problems(
    cfg: ExperimentConfig, method: str, store: ArtifactStore, record: FreezeRecord
) -> list[str]:
    """Libreria, file di configurazione e soglie aggregate rispetto al record."""
    from bench.pipeline.stages.watermark import make_adapter

    problems: list[str] = []
    adapter = make_adapter(cfg, method)
    commit = adapter.source_commit()
    if commit != record.library_commit:
        problems.append(f"patched library at {commit}, frozen at {record.library_commit}")
    patches = patches_sha256(adapter)
    if patches != record.library_patches_sha256:
        problems.append(f"patches of the library {patches}, frozen "
                        f"{record.library_patches_sha256}")  # fmt: skip
    now = config_files_sha256(method)
    for name in sorted(set(now) | set(record.files_sha256)):
        before = record.files_sha256.get(name)
        if now.get(name) != before:
            problems.append(
                f"configs/{name} changed after the freeze: " f"{before} -> {now.get(name)}"
            )
    for path, sha in sorted(record.aggregated_sha256.items()):
        got = file_sha256(store.root / path)
        if got != sha:
            problems.append(f"aggregated thresholds {path} changed ({sha} -> {got})")
    return problems


def check_frozen(cfg: ExperimentConfig, method: str, store: ArtifactStore) -> FreezeRecord:
    """Controlli della parte di test per un metodo del registro (punto 3 di v1.3.16).

    Raises:
        ConfigError: con l'elenco di tutto quello che non va.
    """
    hpo = cfg.hpo
    problems: list[str] = []
    if hpo.grid is not None:
        problems.append(f"hpo.grid={hpo.grid} is not allowed on the test split")
    if hpo.points is not None:
        problems.append("hpo.points is not allowed on the test split")
    if not hpo.use_selected:
        problems.append("hpo.use_selected=false is not allowed on the test split")
    record = load_freeze(store, method)
    if record is None:
        problems.append(f"{method} is not frozen: run `python scripts/freeze_method.py {method}`")
    elif record.library_commit is None:
        problems.append(f"freeze/{method}.json has no library commit (not a post-hoc freeze)")
    else:
        problems += _record_problems(cfg, method, store, record)
    if problems:
        raise ConfigError(
            f"{method} on the test split (P6 freeze check):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    assert record is not None
    return record


def plan_cells(
    cfg: ExperimentConfig, stage: type[Stage], cells: list[Cell], guard: bool = True
) -> list[Cell]:
    """Celle dei metodi del registro (vedi la docstring del modulo); le altre restano uguali.

    ``guard=False`` (solo stime in sola lettura) salta i controlli e usa gli hash del registro.

    Raises:
        ConfigError: controllo sul test fallito o hash non registrato.
    """
    from dataclasses import replace

    registry = load_registry()
    if not any(c.method in registry.methods for c in cells):
        return cells
    frozen_stage = bool(getattr(stage, "frozen_config", False))
    store = ArtifactStore(cfg.paths.artifacts)
    records: dict[str, FreezeRecord | None] = {}

    def record_of(method: str, test: bool) -> FreezeRecord | None:
        key = f"{method}:{test and guard}"
        if key not in records:
            records[key] = (
                check_frozen(cfg, method, store) if test and guard else load_freeze(store, method)
            )
        return records[key]

    out: list[Cell] = []
    for cell in cells:
        method = cell.method
        test = cell.split == "test"
        if (
            method not in registry.methods
            or cell.model_id is None
            or cell.language is None
            or not (test or frozen_stage)
        ):
            out.append(cell)
            continue
        record = record_of(method, test)
        if record is not None and record.library_commit is not None:
            frozen = record.cells_for(cell.model_id, str(cell.language))
            if any(c.not_applicable for c in frozen):
                out.append(cell)  # linguaggio non applicabile: senza hash, come _selected
                continue
            hashes = [str(c.config_hash) for c in frozen if c.config_hash]
        else:
            hashes = list(registry.labels(method).values())
        if not hashes:
            raise ConfigError(f"{method} {cell.model_id} {cell.language}: no frozen configuration")
        if cell.config_hash is not None:
            if cell.config_hash not in hashes:
                raise ConfigError(
                    f"{cell.key()}: config_hash {cell.config_hash} is not registered for "
                    f"{method} (configs/freeze/posthoc.yaml, freeze/{method}.json): {hashes}"
                )
            out.append(cell)
            continue
        out.extend(replace(cell, config_hash=h) for h in hashes)
    return out
