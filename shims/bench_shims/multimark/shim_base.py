"""Shim comune di ``mcss`` e ``multimark`` (PIPELINE_metodo.md di multimark, P2). Compatibile con
Python 3.9.

Gira nell'ambiente ``posthoc``. Il ``PYTHONPATH`` è ``shims/`` più ``build/patched/multimark``,
cioè la copia del submodule fatta da ``scripts/apply_patches.sh multimark``, senza patch. Il
pacchetto ``multimark`` si importa da lì: nessun calcolo è reimplementato in questo file.

``request.hparams`` contiene i campi nativi di ``multimark.config.Config``: i nomi di §3.11 più
``message_bits`` (24) e ``registry_seed`` (il ``global_seed``). La chiave è ``request.key``.

- **embed**: un item per campione della baseline, con ``code`` e ``expected_message`` (24 bit),
  passati a ``multimark.embed.embed``.
  - Stato (§3.9): ``OK`` se N ≥ B, ``PARTIAL`` se 0 < N < B, ``FAILED`` se N = 0, se il codice
    ha un errore di sintassi o se la rilettura non torna.
  - Il motivo del fallimento va in ``error`` e in ``extra["reason"]``.
  - In ``extra`` restano i campi della libreria (``n_sites``, ``n_sites_by_channel``,
    ``skipped_sites``, ``n_renamed``, ...).
- **detect**: ``multimark.detect.detect`` con il messaggio atteso dell'item, il
  ``registry_seed`` e il nome del metodo (esche ``<metodo>-registry``, §3.10.1).
  - ``score`` è il punteggio principale (``N_reg = 1``).
  - ``decoded_message`` è la decodifica aperta.
  - ``native_decision`` è la diagnostica di §3.10.2 (per ``mcss``: §4.2 punto 5).
  - ``extra`` ha i campi di §3.10.4.
  - ``FAILED`` con motivo ``no_sites`` o ``syntax_error``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import DetectStatus, EmbedStatus

from bench_shims._common.base import ShimBase

EMBED_STATUS = {"OK": EmbedStatus.OK, "PARTIAL": EmbedStatus.PARTIAL, "FAILED": EmbedStatus.FAILED}
DETECT_STATUS = {"OK": DetectStatus.OK, "FAILED": DetectStatus.FAILED}


class PosthocShim(ShimBase):
    """Inserimento e rilevazione con la libreria ``multimark``; ``method`` sceglie il profilo
    (lo decide l'adapter negli iperparametri) e l'etichetta delle esche del registro."""

    method = ""

    def setup(self, request: WorkerRequest) -> None:
        import multimark
        from multimark.config import Config
        from multimark.detect import detect
        from multimark.embed import default_proposer, embed

        hp: Dict[str, Any] = dict(request.hparams)
        self.registry_seed = int(hp.pop("registry_seed"))
        self.cfg = Config(registry_seed=self.registry_seed, **hp)
        self.key = int(request.key)
        self.proposer = default_proposer(self.cfg)  # StaticProposer fino a P3
        self.version = multimark.__version__
        self.library_path = str(multimark.__file__)
        self._embed = embed
        self._detect = detect

    def _check(self, item: WorkerItem) -> str:
        if item.expected_message is None:
            raise ValueError(f"{self.method}: item {item.item_id} has no expected_message")
        return str(item.expected_message)

    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        message = self._check(item)
        start = time.perf_counter()
        res = self._embed(item.code or "", message, self.key, self.cfg, self.proposer)
        extra: Dict[str, Any] = dict(res.extra)
        extra["reason"] = res.reason
        status = EMBED_STATUS[res.status]
        return [
            self.result(
                item,
                status,
                code=res.code,
                extra=extra,
                error=res.reason if status == EmbedStatus.FAILED else None,
                elapsed_s=time.perf_counter() - start,
            )
        ]

    def detect(self, item: WorkerItem) -> WorkerResult:
        message = self._check(item)
        start = time.perf_counter()
        res = self._detect(
            item.code or "",
            message,
            self.key,
            self.cfg,
            registry_seed=self.registry_seed,
            method=self.method,
        )
        extra: Dict[str, Any] = dict(res.extra)
        extra["reason"] = res.reason
        status = DETECT_STATUS[res.status]
        return self.result(
            item,
            status,
            score=res.score,
            native_decision=res.native_decision,
            decoded_message=res.decoded_message,
            extra=extra,
            error=res.reason if status == DetectStatus.FAILED else None,
            elapsed_s=time.perf_counter() - start,
        )
