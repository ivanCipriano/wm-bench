"""Derivazione deterministica dei semi, identica per orchestratore e worker (SPEC §18)."""

from __future__ import annotations

import hashlib


def derive_seed(*parts: object) -> int:
    """Deriva un seme intero in [0, 2**31 - 1) dalle parti fornite.

    Le parti sono convertite con ``str`` e unite da ``|``; il seme sono i primi 8 byte
    dello SHA-256 (big endian) ridotti modulo ``2**31 - 1``. Il risultato non dipende
    dalla versione di Python né da ``PYTHONHASHSEED``.

    Args:
        *parts: componenti del seme, di norma ``global_seed`` seguito da etichette e
            identificativi (es. ``20261001, "split", "humaneval/0"``).

    Returns:
        Il seme derivato.
    """
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**31 - 1)
