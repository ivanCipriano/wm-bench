"""Stringhe costanti degli stati scambiati con i worker (SPEC §5.1).

Sono costanti stringa e non ``Enum`` per la massima portabilità in JSON.
"""

from __future__ import annotations

from typing import ClassVar


class EmbedStatus:
    """Esito dell'inserimento del watermark per un campione."""

    OK = "OK"  # watermark inserito come previsto
    PARTIAL = "PARTIAL"  # inserito parzialmente (es. MCGMark: meno di 24 bit)
    FAILED = "FAILED"  # inserimento impossibile o errore -> conta come non rilevato (I2)
    NOT_APPLICABLE = "NOT_APPLICABLE"  # linguaggio non supportato (es. ACW su Java)

    ALL: ClassVar[frozenset[str]] = frozenset({OK, PARTIAL, FAILED, NOT_APPLICABLE})


class DetectStatus:
    """Esito della rilevazione per un campione."""

    OK = "OK"
    FAILED = "FAILED"  # errore del rilevatore -> punteggio minimo
    NOT_APPLICABLE = "NOT_APPLICABLE"

    ALL: ClassVar[frozenset[str]] = frozenset({OK, FAILED, NOT_APPLICABLE})


class WorkerOp:
    """Operazioni richieste a un worker."""

    EMBED = "embed"  # da prompt (metodi in generazione) o da codice (ACW)
    DETECT = "detect"

    ALL: ClassVar[frozenset[str]] = frozenset({EMBED, DETECT})
