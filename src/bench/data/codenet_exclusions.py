"""Problemi CodeNet esclusi dalla selezione della M9 (``configs/dataset/codenet_excluded.yaml``).

I tre problemi dell'oracle di MCGMark su codice lungo (decisione dell'utente del 7 ottobre 2026)
servono solo alla verifica dell'integrazione: la selezione dei 250 problemi deve escluderli
(``check_not_excluded``), così non entrano in nessuna metrica né nell'HPO.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

from bench.domain.errors import ConfigError, DataError

USES = frozenset({"oracle_mcgmark"})


def load_excluded(path: Path) -> dict[str, str]:
    """``problem_key`` → uso, dal file versionato.

    Raises:
        ConfigError: se il file è malformato, un uso è sconosciuto o una chiave è ripetuta.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("excluded")
    if not isinstance(entries, list):
        raise ConfigError(f"{path}: 'excluded' must be a list")
    result: dict[str, str] = {}
    for entry in entries:
        key, use = entry.get("problem_key"), entry.get("use")
        if not isinstance(key, str) or not key.startswith("codenet/p"):
            raise ConfigError(f"{path}: invalid problem_key {key!r}")
        if use not in USES:
            raise ConfigError(f"{path}: unknown use {use!r} for {key}")
        if not entry.get("reason"):
            raise ConfigError(f"{path}: missing reason for {key}")
        if key in result:
            raise ConfigError(f"{path}: duplicate {key}")
        result[key] = use
    return result


def check_not_excluded(problem_keys: Iterable[str], excluded: dict[str, str]) -> None:
    """Da chiamare nella selezione della M9.

    Raises:
        DataError: se un problema selezionato è escluso.
    """
    clash = sorted(set(problem_keys) & set(excluded))
    if clash:
        raise DataError(f"excluded CodeNet problems selected: {clash}")
