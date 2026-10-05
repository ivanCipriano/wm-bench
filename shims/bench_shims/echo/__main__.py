"""Shim di prova senza modello (test del protocollo e doctor). Compatibile con Python 3.9.

Comportamento guidato da ``request.hparams``:
- ``sleep_s``: attesa per item (per i test di timeout e di uccisione del worker);
- ``fail_items``: ``item_id`` per cui l'operazione solleva un'eccezione;
- ``drop_items``: ``item_id`` per cui lo shim restituisce un risultato in meno (I1);
- ``setup_error``: se vero, ``setup`` fallisce (exit code 2);
- ``marker_dir``: cartella dove scrivere un file per ogni item elaborato (per contare le
  esecuzioni e verificare che la ripresa non rifaccia lavoro).
"""

from __future__ import annotations

import os
import time
from typing import List

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import DetectStatus, EmbedStatus

from bench_shims._common.base import ShimBase
from bench_shims._common.runner import main


class EchoShim(ShimBase):
    """Restituisce il prompt o il codice ricevuto; il punteggio è la lunghezza del codice."""

    method = "echo"

    def setup(self, request: WorkerRequest) -> None:
        self.hp = dict(request.hparams)
        if self.hp.get("setup_error"):
            raise RuntimeError("simulated setup failure")

    def _tick(self, item: WorkerItem) -> None:
        marker_dir = self.hp.get("marker_dir")
        if marker_dir:
            safe = item.item_id.replace("/", "_")
            path = os.path.join(marker_dir, f"{safe}.{time.time_ns()}")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("done")
        time.sleep(float(self.hp.get("sleep_s", 0)))
        if item.item_id in self.hp.get("fail_items", []):
            raise ValueError(f"simulated failure on {item.item_id}")

    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        self._tick(item)
        text = (item.prompt_messages or [{"content": item.code or ""}])[-1]["content"]
        n = item.n if item.prompt_messages is not None else 1
        if item.item_id in self.hp.get("drop_items", []):
            n -= 1
        return [
            self.result(
                item,
                EmbedStatus.OK,
                sample_index=i if item.prompt_messages else None,
                raw_output=f"```\n{text}\n```",
                extra={"seed": item.seed},
            )
            for i in range(n)
        ]

    def detect(self, item: WorkerItem) -> WorkerResult:
        self._tick(item)
        return self.result(item, DetectStatus.OK, score=float(len(item.code or "")))


if __name__ == "__main__":
    raise SystemExit(main(EchoShim))
