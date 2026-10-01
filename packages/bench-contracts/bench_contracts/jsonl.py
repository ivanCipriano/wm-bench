"""Lettura e scrittura JSONL in streaming (scambio di file con i worker, SPEC §8).

La scrittura per riga fa ``flush`` + ``os.fsync``: un worker interrotto lascia un file
con righe complete, da cui si riprende (``read_done_ids``). Un'eventuale ultima riga
troncata viene ignorata in lettura.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from typing import IO, Any, Union

PathLike = Union[str, "os.PathLike[str]"]


def _dump(record: Mapping[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _sync(handle: IO[str]) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def append_jsonl(path: PathLike, record: Mapping[str, Any]) -> None:
    """Aggiunge una riga al file e la rende persistente su disco prima di tornare."""
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(_dump(record) + "\n")
        _sync(handle)


def write_jsonl(path: PathLike, records: Iterable[Mapping[str, Any]]) -> int:
    """Scrive (sovrascrivendo) tutte le righe e restituisce quante ne ha scritte."""
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(_dump(record) + "\n")
            count += 1
        _sync(handle)
    return count


def iter_jsonl(path: PathLike) -> Iterator[dict[str, Any]]:
    """Itera sulle righe del file come dizionari.

    Le righe vuote si saltano. Un'ultima riga senza ``\\n`` finale e non decodificabile
    (scrittura interrotta) si ignora; qualunque altra riga malformata solleva errore.

    Raises:
        ValueError: se una riga intermedia non è JSON valido o non è un oggetto.
    """
    with open(path, encoding="utf-8", newline="") as handle:
        lines = handle.readlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            if index == len(lines) - 1 and not line.endswith("\n"):
                return
            raise ValueError("%s: invalid JSON at line %d" % (path, index + 1)) from None
        if not isinstance(obj, dict):
            raise ValueError("%s: line %d is not a JSON object" % (path, index + 1))
        yield obj


def read_done_ids(path: PathLike, key: str = "item_id") -> set[str]:
    """Restituisce gli identificativi già presenti in un file di output (ripresa).

    Se il file non esiste restituisce l'insieme vuoto.
    """
    if not os.path.exists(path):
        return set()
    return {str(record[key]) for record in iter_jsonl(path) if key in record}
