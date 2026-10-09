"""Adapter di ACW (SPEC §9.2; audit ``docs/audit/acw.md``)."""

from __future__ import annotations

from typing import Any, ClassVar

from bench_contracts import WorkerResult

from bench.domain.enums import MethodFamily
from bench.domain.errors import ConfigError
from bench.methods.base import CodeEmbedder, Detector
from bench.registry import METHODS

PROTOCOL = ("num_transforms",)
N_RULES = 43  # regole 1-45 del codice senza 5 e 10 (``WatermarkInjector.rule_pool``)


@METHODS.register("acw")
class AcwAdapter(CodeEmbedder, Detector):
    """ACW: trasformazioni idempotenti sul codice della baseline (Sourcery e regole proprie)."""

    name: ClassVar[str] = "acw"
    family: ClassVar[MethodFamily] = MethodFamily.POST_HOC
    gpu_for_embed: ClassVar[bool] = False
    gpu_for_detect: ClassVar[bool] = False
    secrets: ClassVar[tuple[str, ...]] = ("SOURCERY_TOKEN",)  # mai nei file (cluster_info §1)

    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        """Numero di trasformazioni; il sottoinsieme lo sceglie la chiave (seme del codice)."""
        unknown = sorted(set(hp) - set(PROTOCOL))
        if unknown:
            raise ConfigError(f"acw: unknown hyperparameters {unknown}")
        if "num_transforms" not in hp:
            raise ConfigError("acw: missing hyperparameters ['num_transforms']")
        n = int(hp["num_transforms"])
        if not 1 <= n <= N_RULES:
            raise ConfigError(f"acw: num_transforms must be in [1, {N_RULES}], got {n}")
        # random_rules=True come la CLI del codice (refactor.py main): ordine mescolato dal seme.
        return {"num_transforms": n, "random_rules": True}

    def embed_metrics(self, result: WorkerResult | None) -> dict[str, Any]:
        """Siti idonei = regole selezionate che modificano il codice della baseline (audit §2)."""
        metrics = super().embed_metrics(result)
        extra = result.extra if result is not None else {}
        metrics["n_sites"] = extra.get("n_applicable")
        return metrics
