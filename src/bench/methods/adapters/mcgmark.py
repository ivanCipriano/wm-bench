"""Adapter di MCGMark (SPEC §9.5; audit ``docs/audit/mcgmark.md``)."""

from __future__ import annotations

from typing import Any, ClassVar

from bench_contracts import WorkerResult

from bench.config.schema import ModelSpec
from bench.domain.enums import MethodFamily
from bench.domain.errors import ConfigError
from bench.domain.ids import derive_seed
from bench.domain.models import Problem
from bench.methods.base import Detector, PromptEmbedder
from bench.registry import METHODS

PREFILL = "```python\n"  # fence di apertura e a capo (D20)
MESSAGE_BITS = 12  # payload d'informazione (24 bit = 12 + 12 di correzione, D1)
# Configurazione fissa (audit §3): il bias è lo scarto massimo dei logit (paper §4.2) e γ è
# fissato a 0,5 dalla patch 0001 (D18); delta è ignorato dal repository e resta al suo default.
FIXED_NATIVE: dict[str, Any] = {
    "gamma": 0.5,
    "delta": 6.0,
    "seeding_scheme": "simple_1",
    "select_green_tokens": True,
    # Adattamento al formato chat (D20): il turno dell'assistente inizia con la fence, così il
    # codice generato non resta bloccato dalla macchina a stati (``` trattata come docstring).
    "assistant_prefill": PREFILL,
}


def message_bits(
    global_seed: int, problem_key: str, language: str, model_id: str, index: int | None
) -> str:
    """Messaggio atteso di 12 bit (SPEC §9.5): anche per i negativi, dai loro identificativi."""
    value = derive_seed(global_seed, "mcgmark-msg", problem_key, language, model_id, index)
    return format(value % (2**MESSAGE_BITS), f"0{MESSAGE_BITS}b")


@METHODS.register("mcgmark")
class McgmarkAdapter(PromptEmbedder, Detector):
    """MCGMark: multi-bit, un campione per item, rilevazione su CUDA (generatore casuale)."""

    name: ClassVar[str] = "mcgmark"
    family: ClassVar[MethodFamily] = MethodFamily.LOGIT
    gpu_for_embed: ClassVar[bool] = True
    gpu_for_detect: ClassVar[bool] = True  # torch.Generator sul dispositivo (audit §7)
    one_sample_per_item: ClassVar[bool] = True  # niente batch: stato globale (audit §7)
    twin_baseline: ClassVar[bool] = True  # baseline gemella con lo stesso prefill (D20)

    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        """Nessun iperparametro regolabile nel codice né nel paper (audit §3)."""
        if hp:
            raise ConfigError(f"mcgmark has no tunable hyperparameters, got {sorted(hp)}")
        return dict(FIXED_NATIVE)

    def embed_metrics(self, result: WorkerResult | None) -> dict[str, Any]:
        """Siti idonei = posizioni marcate (unità: token generato, audit §2)."""
        metrics = super().embed_metrics(result)
        extra = result.extra if result is not None else {}
        # Baseline gemella: nessun sito.
        metrics["n_sites"] = None if extra.get("watermark") is False else extra.get("n_embedded")
        return metrics

    def message_for(
        self, model: ModelSpec, problem_key: str, language: str, index: int | None
    ) -> str | None:
        return message_bits(self.cfg.global_seed, problem_key, language, model.model_id, index)

    def expected_message(self, model: ModelSpec, problem: Problem, index: int) -> str | None:
        return message_bits(
            self.cfg.global_seed, problem.problem_key, str(problem.language), model.model_id, index
        )
