"""Attacchi di robustezza: interfaccia, contesto, esito e griglie dei parametri (SPEC §7.7, §12).

Ogni attacco è una sottoclasse di :class:`Attack` registrata in ``bench.registry.ATTACKS`` con il
suo ID (``"T1.4"``). I parametri vengono da ``configs/attack/<file>.yaml``::

    attack_id: T1.4
    space:                 # prodotto cartesiano: una cella per combinazione
      ratio: [0.25, 0.5, 0.75, 1.0]
      name_kind: [random, natural]

oppure un elenco esplicito ``points`` (combinazioni con parametri diversi, come T1.3).

Ogni combinazione ha un ``attack_params_hash`` (12 caratteri) del JSON canonico di ID, parametri
e versione dell'implementazione: correggere un attacco (``version``) cambia l'hash e quindi le
celle, invece di mescolare risultati di implementazioni diverse.

**Determinismo:** l'RNG di un campione nasce da
``derive_seed(global_seed, "attack", attack_id, params_hash, sample_id)`` (SPEC §18).
"""

from __future__ import annotations

import itertools
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from bench.config.builder import config_dir
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.domain.ids import derive_seed
from bench.domain.models import Problem
from bench.lang.parsers import ParserService
from bench.store.hashing import sha256_json

if TYPE_CHECKING:
    from bench.attacks.tools import ToolRunner

logger = logging.getLogger(__name__)

ALL_LANGUAGES: frozenset[Language] = frozenset(Language)
PARAMS_HASH_LENGTH = 12


@dataclass(frozen=True)
class AttackPools:
    """Raccolte di commenti e nomi umani di un linguaggio (fase ``attack_pools``)."""

    comments: tuple[str, ...] = ()
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttackContext:
    """Dipendenze di un attacco per un campione.

    Attributes:
        rng: generatore del campione (seme di SPEC §18).
        problem: problema del campione (``entry_point`` da non rinominare), se noto.
        parsers: parser tree-sitter.
        tools: esecutore dei tool esterni (formatter e linter).
        pools: raccolte di commenti e nomi del linguaggio.
        sample_id: identificativo del campione attaccato (diagnostica).
    """

    rng: random.Random
    problem: Problem | None
    parsers: ParserService
    tools: ToolRunner | None = None
    pools: AttackPools = field(default_factory=AttackPools)
    sample_id: str = ""


@dataclass(frozen=True)
class AttackOutcome:
    """Esito di un attacco su un campione: codice (l'originale se non applicato) e dettagli."""

    code: str
    status: AttackStatus
    detail: dict[str, Any] = field(default_factory=dict)


def outcome(original: str, new: str, detail: dict[str, Any] | None = None) -> AttackOutcome:
    """``APPLIED`` se il codice è cambiato, altrimenti ``UNCHANGED`` con il codice originale."""
    if new == original:
        return AttackOutcome(original, AttackStatus.UNCHANGED, dict(detail or {}))
    return AttackOutcome(new, AttackStatus.APPLIED, dict(detail or {}))


def tool_error(original: str, error: str, detail: dict[str, Any] | None = None) -> AttackOutcome:
    """Errore del tool o dell'attacco: codice originale, stato ``TOOL_ERROR`` (I5: il campione si
    esegue e si rileva comunque)."""
    return AttackOutcome(
        original, AttackStatus.TOOL_ERROR, {**(detail or {}), "error": error[:2000]}
    )


def attack_params_hash(attack_id: str, params: Mapping[str, Any], version: int) -> str:
    """Hash (12 caratteri) di ID, parametri e versione dell'implementazione."""
    payload = {"attack_id": attack_id, "params": dict(params), "version": version}
    return sha256_json(payload)[:PARAMS_HASH_LENGTH]


class Attack(ABC):
    """Attacco di robustezza (SPEC §7.7).

    Args:
        params: parametri della cella (una combinazione della griglia).
    """

    attack_id: ClassVar[str]
    group: ClassVar[str]
    supported_languages: ClassVar[frozenset[Language]] = ALL_LANGUAGES
    requires_gpu: ClassVar[bool] = False
    needs_pools: ClassVar[bool] = False
    needs_tools: ClassVar[bool] = False
    version: ClassVar[int] = 1

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = dict(params)

    def params_hash(self) -> str:
        """``attack_params_hash`` della cella."""
        return attack_params_hash(self.attack_id, self.params, self.version)

    def supports(self, language: Language | str) -> bool:
        """Se l'attacco si applica al linguaggio (altrimenti ``NOT_APPLICABLE``)."""
        return Language(language) in self.supported_languages

    @abstractmethod
    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        """Applica l'attacco a un campione."""

    def apply_many(
        self, codes: Sequence[str], language: Language, ctxs: Sequence[AttackContext]
    ) -> list[AttackOutcome]:
        """Applica l'attacco a più campioni (i tool esterni lo ridefiniscono per lavorare a
        lotti). Un errore su un campione non ferma gli altri: diventa ``TOOL_ERROR``."""
        out: list[AttackOutcome] = []
        for code, ctx in zip(codes, ctxs, strict=True):
            out.append(self.safe_apply(code, language, ctx))
        return out

    def safe_apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        """``apply`` con i casi comuni: linguaggio non supportato, codice vuoto, eccezioni."""
        if not self.supports(language):
            return AttackOutcome(code, AttackStatus.NOT_APPLICABLE, {})
        if not code.strip():
            return AttackOutcome(code, AttackStatus.UNCHANGED, {"empty": True})
        try:
            return self.apply(code, language, ctx)
        except Exception as exc:  # un campione non deve fermare la cella
            logger.warning(
                "%s on %s: %s: %s", self.attack_id, ctx.sample_id, type(exc).__name__, exc
            )
            return tool_error(code, f"{type(exc).__name__}: {exc}")


def sample_rng(global_seed: int, attack_id: str, params_hash: str, sample_id: str) -> random.Random:
    """RNG di un campione attaccato (SPEC §18)."""
    return random.Random(derive_seed(global_seed, "attack", attack_id, params_hash, sample_id))


# ---------------------------------------------------------------------- griglie dei parametri
@dataclass(frozen=True)
class AttackGrid:
    """Griglia di un attacco: file di configurazione e combinazioni di parametri."""

    attack_id: str
    path: Path
    points: tuple[dict[str, Any], ...]


def _product(space: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    if not space:
        return [{}]
    keys = list(space)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*space.values())]


@cache
def _grids(root: Path) -> dict[str, AttackGrid]:
    grids: dict[str, AttackGrid] = {}
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        attack_id = str(data.get("attack_id") or "")
        if not attack_id:
            raise ConfigError(f"{path}: attack_id is required")
        if attack_id in grids:
            raise ConfigError(
                f"{path}: attack {attack_id} already defined in {grids[attack_id].path}"
            )
        space = {str(k): list(v) for k, v in (data.get("space") or {}).items()}
        fixed = dict(data.get("fixed") or {})
        explicit = [dict(p) for p in (data.get("points") or [])]
        if explicit and space:
            raise ConfigError(f"{path}: use either space or points, not both")
        points = tuple({**fixed, **p} for p in (explicit or _product(space)))
        grids[attack_id] = AttackGrid(attack_id, path, points)
    return grids


def attack_grid(attack_id: str, root: Path | None = None) -> AttackGrid:
    """Griglia di un attacco da ``configs/attack/*.yaml``.

    Raises:
        ConfigError: attacco senza file di configurazione.
    """
    grids = _grids(root or config_dir() / "attack")
    if attack_id not in grids:
        raise ConfigError(f"no configs/attack/*.yaml for attack {attack_id} ({sorted(grids)})")
    return grids[attack_id]


def make_attack(attack_id: str, params: Mapping[str, Any]) -> Attack:
    """Istanza registrata di un attacco con i parametri dati."""
    import bench.attacks.t1  # noqa: F401  (popola ATTACKS)
    from bench.registry import ATTACKS

    return ATTACKS.get(attack_id)(params)


def grid_attacks(attack_id: str, root: Path | None = None) -> dict[str, Attack]:
    """``attack_params_hash`` → attacco, per tutte le combinazioni della griglia."""
    out: dict[str, Attack] = {}
    for params in attack_grid(attack_id, root).points:
        attack = make_attack(attack_id, params)
        out[attack.params_hash()] = attack
    return out


def resolve_attack(attack_id: str, params_hash: str | None) -> Attack:
    """Attacco di una cella dal suo hash.

    Raises:
        ConfigError: hash mancante o fuori dalla griglia.
    """
    attacks = grid_attacks(attack_id)
    if params_hash is None or params_hash not in attacks:
        raise ConfigError(
            f"attack {attack_id}: attack_params_hash {params_hash} is not in the grid "
            f"({sorted(attacks)})"
        )
    return attacks[params_hash]
