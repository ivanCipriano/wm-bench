"""Fixture condivise della suite di test."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from bench.config.schema import ExperimentConfig
    from bench.doctor import CommandResult

REPO_ROOT = Path(__file__).resolve().parent.parent

# Valori attesi di derive_seed, fissati una volta per tutte (SPEC §21.3): devono
# coincidere in ogni versione di Python (3.9, 3.10, 3.11) e in ogni ambiente.
SEED_VECTORS: list[tuple[list[Any], int]] = [
    ([20261001, "split", "humaneval/0"], 614248956),
    ([20261001, "gen", "qwen25_coder_7b", "mbpp/17", "python"], 351847650),
    ([20261001, "wm-key", "sweet", "k1"], 1371034547),
    ([20261001, "mcgmark-msg", "humaneval/42", "java", "deepseek_coder_6p7b", 3], 84821436),
    ([], 1616749724),
    (["àèì", None, 1.5], 1553620446),
]


@pytest.fixture
def repo_root() -> Path:
    """Radice del repository."""
    return REPO_ROOT


@pytest.fixture
def local_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Radice temporanea per ``paths=local`` (artefatti, tmp, dataset, cache HF)."""
    root = tmp_path / "wmb"
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(root))
    return root


@pytest.fixture
def cfg(local_root: Path) -> ExperimentConfig:
    """Configurazione reale di ``configs/`` con ``paths=local`` sotto una cartella temporanea."""
    from bench.config.builder import load_experiment

    return load_experiment(["paths=local", "stage=selftest"])


def rebuild(cfg: ExperimentConfig, **overrides: Any) -> ExperimentConfig:
    """Ricostruisce una configurazione cambiando campi di primo livello."""
    from bench.config.builder import ExperimentBuilder

    data = cfg.model_dump(mode="json")
    data.update(overrides)
    return ExperimentBuilder.from_dict(data).build()


@dataclass
class FakeRunner:
    """Esecutore di comandi finto per il doctor: risposte per prefisso del comando."""

    responses: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    fallback: Callable[[tuple[str, ...]], CommandResult | None] | None = None

    def __call__(
        self,
        cmd: Sequence[str],
        timeout: float,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> CommandResult:
        from bench.doctor import CommandResult

        key = tuple(cmd)
        self.calls.append(key)
        for prefix in sorted(self.responses, key=len, reverse=True):
            if key[: len(prefix)] == prefix:
                return self.responses[prefix]
        if self.fallback is not None:
            result = self.fallback(key)
            if result is not None:
                return result
        return CommandResult(127, "", f"no fake response for {key}")
