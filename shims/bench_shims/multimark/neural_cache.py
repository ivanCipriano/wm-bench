"""Cache su disco delle risposte del modello dei nomi (proponente neurale di multimark, P3, MD31).
Compatibile con Python 3.9.

Ogni cella gira in un suo processo worker, quindi la cache in memoria di ``NeuralProposer`` non si
condivide fra le configurazioni (B e D del giro P3 leggono gli stessi codici della baseline). Questa
cache avvolge il generatore che lo shim passa a ``NeuralProposer(generate=...)`` (parametro
pubblico della libreria): un file per **lotto intero** di prompt, perché con il riempimento a
sinistra la risposta a un prompt dipende dal lotto in cui sta.

La chiave (decisione dell'utente del 22 novembre 2026) è lo SHA-256 di:

- il lotto: i messaggi chat di ogni prompt, nell'ordine (il prompt di sistema e quello dell'utente,
  con il codice e la variabile, sono dentro i messaggi);
- il contesto: modello con la snapshot, ``dtype``, parametri di generazione (greedy,
  ``max_new_tokens``), numero di nomi chiesti, dimensione dei lotti, commit della libreria, GPU,
  versioni di ``torch`` e ``transformers``.

Se una sola di queste cose cambia la chiave è un'altra e la cache non si riusa. Il file contiene
anche contesto e lotto: alla lettura si confrontano con quelli richiesti (un file diverso conta come
assente). La scrittura è atomica (file temporaneo e ``os.replace``), quindi due worker che scrivono
lo stesso lotto non si rovinano a vicenda: le risposte sono le stesse.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

Messages = List[Dict[str, str]]


def _dumps(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class DiskCachedGenerator:
    """Generatore ``lotto di messaggi -> testi`` con cache su disco per lotto (vedi il modulo)."""

    def __init__(
        self,
        generate: Callable[[Sequence[Messages]], List[str]],
        cache_dir: str,
        context: Dict[str, Any],
    ) -> None:
        self.generate = generate
        self.dir = Path(cache_dir)
        self.context = dict(context)
        #: lotti letti dalla cache e lotti generati dal modello (per gli ``extra`` dello shim)
        self.hits = 0
        self.misses = 0

    def key(self, batch: Sequence[Messages]) -> str:
        payload = _dumps({"context": self.context, "batch": [list(m) for m in batch]})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def _read(self, key: str, batch: Sequence[Messages]) -> Optional[List[str]]:
        path = self.path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        texts = data.get("texts")
        same = (
            data.get("key") == key
            and data.get("context") == self.context
            and data.get("batch") == [list(m) for m in batch]
        )
        if not same or not isinstance(texts, list) or len(texts) != len(batch):
            return None
        return [str(t) for t in texts]

    def _write(self, key: str, batch: Sequence[Messages], texts: List[str]) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        data = {"key": key, "context": self.context, "batch": [list(m) for m in batch],
                "texts": list(texts)}  # fmt: skip
        tmp.write_text(_dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def __call__(self, batch: Sequence[Messages]) -> List[str]:
        if not batch:
            return []
        key = self.key(batch)
        cached = self._read(key, batch)
        if cached is not None:
            self.hits += 1
            return cached
        texts = list(self.generate(batch))
        self.misses += 1
        self._write(key, batch, texts)
        return texts
