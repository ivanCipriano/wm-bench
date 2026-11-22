"""Adapter di ``mcss``: reimplementazione di Multi-Channel Spread-Spectrum (Choi, Gao, Duan, 2026;
arXiv 2607.06009v1) come profilo della libreria ``multimark`` (PIPELINE_metodo.md di multimark,
§4; audit ``docs/audit/mcss.md``).

Stessa base di ``multimark`` (``PosthocMultibitAdapter``): post-hoc, 24 bit, solo Python, stesso
punteggio per la calibrazione all'1% (§4.2 punto 5: MCSS non ha un punteggio continuo). Il profilo
è quello di §3.11, colonna ``mcss``; le deviazioni dal paper sono in §4.2 e nell'audit. Griglia di
``t``: solo 2 (decisione E di §7).
"""

from __future__ import annotations

from typing import Any, ClassVar

from bench.methods.adapters.multimark import PosthocMultibitAdapter
from bench.registry import METHODS


@METHODS.register("mcss")
class McssAdapter(PosthocMultibitAdapter):
    """MCSS: legame posizionale, niente maschera, forme canoniche del paper, nomi V1 (4 stili)."""

    name: ClassVar[str] = "mcss"
    PROFILE: ClassVar[dict[str, Any]] = {
        "t": 2,
        "linking": "positional",
        "mask": "off",
        "canonical": "paper",
        "names.variant": "V1",
        "names.proposer": "static",  # non usato da V1 (stili): fissato (MD31)
        "names.include_params": True,  # §4.2 punto 8
        "channels": "mcss8",
        "names.module_level": True,  # §3.6.1: true per mcss in ogni caso
        "code.matrix": "random",  # MD18: fedeltà, «random by construction»
        "names.single_letter": "extend_except_loop",  # §4.2 punto 9, esito v1.2.7 (MD19)
        "names.bits_per_name": 1,  # un nome, uno stile (v1.3.2, MD27)
        "canonical.same_kind": "off",  # forme del paper (v1.3.2, MD28)
        "canonical.natural_forms": "off",  # solo multimark (v1.3.9, MD29): fissato
        "names.suffix_by_type": "off",  # solo multimark (v1.3.9, MD30): fissato
        "voting": "simple",
        "channel_weights": None,
    }
    # MD29, MD30: opzioni di multimark, mai attive con il profilo mcss (la libreria le rifiuta
    # con canonical=paper; l'adapter le rifiuta anche se si cambiasse canonical). MD31: il
    # proponente neurale è solo di multimark (V1 non usa candidati), quindi resta static.
    FIXED: ClassVar[tuple[str, ...]] = (
        "canonical.natural_forms",
        "names.suffix_by_type",
        "names.proposer",
    )
