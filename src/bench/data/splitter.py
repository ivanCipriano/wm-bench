"""Divisione dev/test per problema (SPEC §7.4, §10.4; invariante I4; D7).

Regola: per ogni famiglia si ordinano i ``problem_key`` per
``derive_seed(global_seed, "split", problem_key)`` (a parità di seme, per chiave) e i primi
``dev`` vanno in sviluppo. L'assegnazione dipende solo dalla chiave, non dal linguaggio, dal
dataset o dall'ordine di ingresso: lo stesso problema ha la stessa parte ovunque.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from bench.config.schema import SplitConfig
from bench.domain.enums import Split
from bench.domain.errors import DataError
from bench.domain.ids import derive_seed
from bench.domain.models import CodeSample, Problem


def family_of(key: str) -> str:
    """Famiglia di un ``problem_key`` (la parte prima di ``/``)."""
    return key.split("/", 1)[0]


class ProblemSplitter:
    """Assegna ``problem_key`` → ``Split``.

    Args:
        cfg: regole di divisione (famiglie con ``dev``/``total``, regola per gli extra MBPP).
        seed: seme globale.
    """

    def __init__(self, cfg: SplitConfig, seed: int) -> None:
        self.cfg = cfg
        self.seed = seed

    def rank(self, key: str) -> tuple[int, str]:
        """Chiave di ordinamento: seme derivato, poi la chiave stessa."""
        return derive_seed(self.seed, "split", key), key

    def _first_k_dev(self, keys: Iterable[str], k: int) -> dict[str, Split]:
        ordered = sorted(set(keys), key=self.rank)
        return {key: Split.DEV if i < k else Split.TEST for i, key in enumerate(ordered)}

    def assign(self, problems: Sequence[Problem]) -> dict[str, Split]:
        """Divisione dei problemi con prompt; le chiavi ripetute (più linguaggi) contano una volta.

        Raises:
            DataError: se una famiglia non ha regola o il numero di problemi non è quello atteso.
        """
        by_family: dict[str, set[str]] = {}
        for problem in problems:
            by_family.setdefault(family_of(problem.problem_key), set()).add(problem.problem_key)
        result: dict[str, Split] = {}
        for family, keys in sorted(by_family.items()):
            rule = self.cfg.families.get(family)
            if rule is None:
                raise DataError(f"no split rule for problem family '{family}'")
            if len(keys) != rule.total:
                raise DataError(f"family '{family}': {len(keys)} problems, expected {rule.total}")
            result.update(self._first_k_dev(keys, rule.dev))
        return result

    def extra_dev_count(self, n_extra: int) -> int:
        """Quanti extra MBPP vanno in sviluppo secondo la regola configurata (D7)."""
        if self.cfg.mbpp_extra_assignment == "dev":
            return n_extra
        if self.cfg.mbpp_extra_assignment == "test":
            return 0
        ref = self.cfg.families[self.cfg.mbpp_extra_reference]
        # Arrotondamento all'intero più vicino (metà per eccesso): 596 * 72 / 378 -> 114.
        return math.floor(n_extra * ref.dev / ref.total + 0.5)

    def assign_extra(self, keys: Iterable[str], assigned: Mapping[str, Split]) -> dict[str, Split]:
        """Divisione delle chiavi che hanno solo negativi (soluzioni MBPP senza MBPP+, D7).

        Args:
            keys: chiavi dei negativi; quelle già in ``assigned`` vengono ignorate.
            assigned: divisione dei problemi con prompt.
        """
        extra = sorted(set(keys) - set(assigned))
        return self._first_k_dev(extra, self.extra_dev_count(len(extra)))


def apply_split(problems: Sequence[Problem], split: Mapping[str, Split]) -> list[Problem]:
    """Copia dei problemi con la parte assegnata.

    Raises:
        DataError: se un problema non ha una parte.
    """
    missing = sorted({p.problem_key for p in problems} - set(split))
    if missing:
        raise DataError(f"problems without split: {missing[:5]}")
    return [p.model_copy(update={"split": split[p.problem_key]}) for p in problems]


def apply_split_samples(
    samples: Sequence[CodeSample], split: Mapping[str, Split]
) -> list[CodeSample]:
    """Copia dei campioni con la parte del loro problema (I4).

    Raises:
        DataError: se un campione non ha una parte.
    """
    missing = sorted({s.problem_key for s in samples} - set(split))
    if missing:
        raise DataError(f"samples without split: {missing[:5]}")
    return [s.model_copy(update={"split": split[s.problem_key]}) for s in samples]
