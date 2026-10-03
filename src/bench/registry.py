"""Registri dei componenti estendibili (Registry + Factory, SPEC §6, §7.1).

I registri si popolano solo all'import dei moduli che definiscono i componenti
(import espliciti, niente scoperta dinamica dal filesystem). Esistono ``STAGES``
(M1) e ``LOADERS`` (M2); gli altri arrivano con le rispettive classi base (ADR-003).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, TypeVar

from bench.domain.errors import ConfigError

if TYPE_CHECKING:
    from bench.data.loaders.base import DatasetLoader
    from bench.pipeline.stage import Stage

T = TypeVar("T")


class Registry(Generic[T]):
    """Registro nome → classe per un tipo di componente.

    Args:
        kind: nome del tipo di componente, usato nei messaggi d'errore.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, type[T]] = {}

    def register(self, name: str) -> Callable[[type[T]], type[T]]:
        """Decoratore che registra una classe con il nome dato.

        Raises:
            ConfigError: se il nome è già registrato.
        """

        def decorator(cls: type[T]) -> type[T]:
            if name in self._items:
                raise ConfigError(f"{self.kind} '{name}' already registered")
            self._items[name] = cls
            return cls

        return decorator

    def get(self, name: str) -> type[T]:
        """Restituisce la classe registrata con quel nome.

        Raises:
            ConfigError: se il nome è ignoto (il messaggio elenca i nomi disponibili).
        """
        try:
            return self._items[name]
        except KeyError:
            available = ", ".join(self.names()) or "<none>"
            raise ConfigError(f"unknown {self.kind} '{name}'; available: {available}") from None

    def names(self) -> list[str]:
        """Nomi registrati, in ordine alfabetico."""
        return sorted(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items


STAGES: Registry[Stage] = Registry("stage")
LOADERS: Registry[DatasetLoader] = Registry("loader")
