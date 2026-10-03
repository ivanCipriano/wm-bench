"""Costruzione validata e immutabile di ``ExperimentConfig`` (Builder, SPEC §6).

Due ingressi equivalenti:

- da Hydra: ``ExperimentBuilder.from_hydra(cfg)`` (CLI) o ``compose_config(overrides)``;
- da codice: ``ExperimentBuilder.from_dict(d).with_stage("...").with_methods([...]).build()``.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig, OmegaConf
from pydantic import ValidationError

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError

REPO_ENV_VAR = "WMB_REPO"


def repo_root() -> Path:
    """Radice del repository: ``$WMB_REPO`` se definita, altrimenti dedotta dal pacchetto.

    Raises:
        ConfigError: se nella radice non c'è ``configs/config.yaml``.
    """
    env = os.environ.get(REPO_ENV_VAR)
    root = Path(env) if env else Path(__file__).resolve().parents[3]
    if not (root / "configs" / "config.yaml").is_file():
        raise ConfigError(f"configs/config.yaml not found under {root}; set ${REPO_ENV_VAR}")
    return root


# Risolutore usato da configs/paths/local.yaml per la radice del repository.
if not OmegaConf.has_resolver("wmb_repo"):
    OmegaConf.register_new_resolver("wmb_repo", lambda: str(repo_root()))


def config_dir() -> Path:
    """Cartella ``configs/`` del repository."""
    return repo_root() / "configs"


def compose_config(overrides: Sequence[str] = (), config_name: str = "config") -> DictConfig:
    """Compone la configurazione Hydra fuori da ``@hydra.main`` (doctor, test, notebook).

    Raises:
        ConfigError: se un override non è valido (con un suggerimento per gli spazi incollati).
    """
    from hydra.errors import HydraException

    GlobalHydra.instance().clear()
    try:
        with initialize_config_dir(config_dir=str(config_dir()), version_base="1.3"):
            return compose(config_name=config_name, overrides=list(overrides))
    except HydraException as exc:
        spaced = [o for o in overrides if any(ch.isspace() for ch in o)]
        hint = ""
        if spaced:
            hint = (
                f"; these overrides contain spaces: {spaced!r}. Usually two arguments were "
                "pasted with a non-breaking space between them: retype the spaces by hand"
            )
        raise ConfigError(f"invalid Hydra override(s) {list(overrides)!r}: {exc}{hint}") from exc


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  {loc}: {err['msg']}")
    return "invalid configuration:\n" + "\n".join(lines)


class ExperimentBuilder:
    """Builder di ``ExperimentConfig``.

    Args:
        data: configurazione come dizionario (già risolta, senza interpolazioni).
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = copy.deepcopy(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentBuilder:
        """Builder da un dizionario."""
        return cls(data)

    @classmethod
    def from_hydra(cls, cfg: DictConfig) -> ExperimentBuilder:
        """Builder da una ``DictConfig`` di Hydra (le interpolazioni vengono risolte).

        Raises:
            ConfigError: se un valore obbligatorio (``???``) manca o un'interpolazione fallisce.
        """
        try:
            container = OmegaConf.to_container(cfg, resolve=True, throw_on_missing=True)
        except Exception as exc:  # omegaconf solleva tipi diversi a seconda del problema
            raise ConfigError(f"cannot resolve configuration: {exc}") from exc
        return cls(cast(dict[str, Any], container))

    def with_overrides(self, **values: Any) -> ExperimentBuilder:
        """Imposta campi di primo livello."""
        self._data.update(copy.deepcopy(values))
        return self

    def with_stage(self, stage: str) -> ExperimentBuilder:
        """Imposta la fase."""
        return self.with_overrides(stage=stage)

    def with_methods(self, methods: Iterable[str]) -> ExperimentBuilder:
        """Imposta i metodi."""
        return self.with_overrides(methods=list(methods))

    def with_models(self, models: Iterable[str]) -> ExperimentBuilder:
        """Imposta i modelli."""
        return self.with_overrides(models=list(models))

    def with_paths(self, **paths: str | Path) -> ExperimentBuilder:
        """Sostituisce alcune radici dei percorsi (es. ``artifacts`` nei test)."""
        current = dict(self._data.get("paths", {}))
        current.update({k: str(v) for k, v in paths.items()})
        self._data["paths"] = current
        return self

    def build(self) -> ExperimentConfig:
        """Valida e restituisce la configurazione immutabile.

        Raises:
            ConfigError: se la configurazione non è valida (messaggio con tutti gli errori).
        """
        try:
            return ExperimentConfig.model_validate(self._data)
        except ValidationError as exc:
            raise ConfigError(_format_validation_error(exc)) from exc


def load_experiment(overrides: Sequence[str] = ()) -> ExperimentConfig:
    """Compone e valida la configurazione con gli override Hydra dati."""
    return ExperimentBuilder.from_hydra(compose_config(overrides)).build()
