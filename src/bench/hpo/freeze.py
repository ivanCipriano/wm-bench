"""Congelamento della configurazione di un metodo prima della parte di test (ADR-013, M10).

Decisione dell'utente del 16 ottobre 2026: un metodo entra nella fase 0 (watermark, esecuzione,
rilevazione e metriche su L1 test) e negli attacchi solo dopo il **congelamento**, possibile se:

- la scelta è quella definitiva su L1+L2 (``hpo/selected_L1L2/``) per ogni (modello, linguaggio);
- le soglie della configurazione scelta sono definitive (``provisional: false``, livelli L1+L2);
- la griglia ``configs/hpo/<metodo>.yaml`` è quella usata dalla scelta (stessi candidati).

Il record ``freeze/<metodo>.json`` è la prova che le scelte sono state fatte prima di vedere il
test: data, commit (nel manifest), configurazione scelta e sha256 di selezione e soglia per cella,
hash della griglia. Dopo il congelamento ``calibrate`` e ``tune`` rifiutano il metodo, salvo
``hpo.allow_frozen_override`` (deviazione da registrare), e le fasi sulla parte di test usano la
configurazione congelata (:func:`selected_hash`, chiamata dal ``CellPlanner``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from bench.domain.errors import ConfigError
from bench.domain.models import FrozenModel
from bench.store.artifact_store import ArtifactStore
from bench.store.hashing import sha256_bytes
from bench.store.refs import ArtifactRef

FINAL_LEVELS: tuple[str, ...] = ("L1", "L2")
MAIN_KEY_ID = "k1"  # chiave principale (SPEC §9.6); k2 solo per T3.1 (ADR-014 §3)


class FrozenCell(FrozenModel):
    """Configurazione congelata di (modello, linguaggio)."""

    model_id: str
    language: str
    not_applicable: bool = False
    config_hash: str | None = None
    hparams: dict[str, Any] = Field(default_factory=dict)
    selected_sha256: str | None = None
    threshold: float | None = None
    threshold_sha256: str | None = None
    constraint_satisfied: bool | None = None


class FreezeRecord(FrozenModel):
    """Congelamento di un metodo: le scelte valide per la parte di test."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", protected_namespaces=(), ser_json_inf_nan="strings"
    )

    method: str
    frozen_at: datetime
    levels: list[str]
    grid_sha256: str | None
    candidate_hashes: list[str]
    cells: list[FrozenCell]

    def cell(self, model_id: str, language: str) -> FrozenCell | None:
        """Cella congelata di (modello, linguaggio), se c'è."""
        for c in self.cells:
            if c.model_id == model_id and c.language == language:
                return c
        return None


def freeze_ref(method: str, key_id: str = MAIN_KEY_ID) -> ArtifactRef:
    """Record di congelamento di un metodo; con una chiave diversa da k1 quello delle soglie di
    quella chiave (``freeze/<metodo>.<key_id>.json``, T3.1)."""
    if key_id == MAIN_KEY_ID:
        return ArtifactRef.of("freeze", f"freeze/{method}.json")
    return ArtifactRef.of(f"freeze_{key_id}", f"freeze/{method}.{key_id}.json")


def load_freeze(
    store: ArtifactStore, method: str, key_id: str = MAIN_KEY_ID
) -> FreezeRecord | None:
    """Record di congelamento del metodo (della chiave), o ``None`` se non è congelato."""
    ref = freeze_ref(method, key_id)
    if not store.exists(ref):
        return None
    return store.read_model(ref, FreezeRecord)


def data_sha256(store: ArtifactStore, ref: ArtifactRef) -> str | None:
    """sha256 dei dati di un artefatto (dal manifest)."""
    manifest = store.read_manifest(ref)
    return manifest.data_sha256 if manifest is not None else None


def file_sha256(path: Any) -> str | None:
    """sha256 di un file di configurazione, ``None`` se non esiste."""
    try:
        with open(path, "rb") as handle:
            return sha256_bytes(handle.read())
    except FileNotFoundError:
        return None


def ensure_not_frozen(
    store: ArtifactStore, method: str, stage: str, override: bool, key_id: str = MAIN_KEY_ID
) -> bool:
    """Rifiuta una fase che cambierebbe soglie o scelta di un metodo congelato.

    Returns:
        ``True`` se il metodo è congelato e si procede per ``override`` (da registrare nel
        manifest e in ``docs/deviations.md``), ``False`` se non è congelato.

    Raises:
        ConfigError: metodo congelato senza ``hpo.allow_frozen_override``.
    """
    record = load_freeze(store, method, key_id)
    if record is None:
        return False
    if not override:
        raise ConfigError(
            f"{method} was frozen on {record.frozen_at:%Y-%m-%d} "
            f"({freeze_ref(method, key_id).path}): "
            f"stage {stage} would change its thresholds or selection. Any change is a deviation: "
            "rerun with hpo.allow_frozen_override=true only after recording it in "
            "docs/deviations.md"
        )
    return True


def selected_hash(store: ArtifactStore, method: str, model_id: str, language: str) -> str | None:
    """Hash della configurazione congelata di (metodo, modello, linguaggio).

    Returns:
        L'hash, oppure ``None`` se il linguaggio non è applicabile per il metodo.

    Raises:
        ConfigError: metodo non congelato o cella assente dal congelamento.
    """
    record = load_freeze(store, method)
    if record is None:
        raise ConfigError(
            f"{method} is not frozen: the test split needs the final configuration. "
            f"Run `python scripts/freeze_method.py {method}` first (ADR-013)"
        )
    cell = record.cell(model_id, language)
    if cell is None:
        raise ConfigError(f"{method}: {model_id} {language} is not in freeze/{method}.json")
    return None if cell.not_applicable else cell.config_hash
