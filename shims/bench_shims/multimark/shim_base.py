"""Shim comune di ``mcss`` e ``multimark`` (PIPELINE_metodo.md di multimark, P2). Compatibile con
Python 3.9.

Gira nell'ambiente ``posthoc``. Il ``PYTHONPATH`` è ``shims/`` più ``build/patched/multimark``,
cioè la copia del submodule fatta da ``scripts/apply_patches.sh multimark``, senza patch. Il
pacchetto ``multimark`` si importa da lì: nessun calcolo è reimplementato in questo file.

``request.hparams`` contiene i campi nativi di ``multimark.config.Config``: i nomi di §3.11 più
``message_bits`` (24) e ``registry_seed`` (il ``global_seed``). La chiave è ``request.key``.

Con ``names.proposer = neural`` (P3, MD31) ci sono in più le chiavi ``names_neural_*`` dell'adapter
(``NEURAL_KEYS``), che non sono campi di ``Config``: nell'inserimento lo shim crea
``HFGenerator`` della libreria sulla snapshot locale (``names_neural_path``, sulla GPU della
richiesta: senza GPU si ferma), lo avvolge nella cache su disco (``neural_cache.py``) e lo passa a
``NeuralProposer``. Il modello si carica in ``setup``, quindi il tempo di caricamento non entra in
``elapsed_s``. Negli ``extra`` di ogni campione, solo con il proponente neurale:
``n_neural_prompts`` (prompt chiesti al generatore), ``n_neural_batches_cached`` e
``n_neural_batches_generated`` (lotti letti dalla cache su disco e generati dal modello). La
rilevazione non usa il modello.

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

# Chiavi native del proponente neurale (adapter), tolte prima di ``Config``.
NEURAL_KEYS = (
    "names_neural_model",
    "names_neural_dtype",
    "names_neural_path",
    "names_neural_cache",
    "names_neural_library",
)
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
        neural = {k: hp.pop(k) for k in NEURAL_KEYS if k in hp}
        self.cfg = Config(registry_seed=self.registry_seed, **hp)
        self.key = int(request.key)
        self.cache: Any = None
        if self.cfg.names_proposer != "neural":
            self.proposer: Any = default_proposer(self.cfg)  # StaticProposer
        elif request.op == "embed":
            self.proposer = self._neural_proposer(request, neural)
        else:
            self.proposer = None  # la rilevazione non usa il proponente
        self.version = multimark.__version__
        self.library_path = str(multimark.__file__)
        self._embed = embed
        self._detect = detect

    def _neural_proposer(self, request: WorkerRequest, neural: Dict[str, Any]) -> Any:
        """``NeuralProposer`` con ``HFGenerator`` della libreria sulla snapshot locale e la cache
        su disco (chiave: lotto, modello con snapshot, prompt, parametri di generazione)."""
        import torch
        import transformers
        from multimark import names_neural

        from bench_shims.multimark.neural_cache import DiskCachedGenerator

        missing = [k for k in NEURAL_KEYS if k not in neural]
        if missing:
            raise ValueError(f"{self.method}: names.proposer=neural without {missing}")
        device = str(request.device)
        if not device.startswith("cuda") or not torch.cuda.is_available():
            raise RuntimeError(
                f"{self.method}: names.proposer=neural needs a CUDA GPU (device {device}, "
                f"cuda available {torch.cuda.is_available()})"
            )
        generator = names_neural.HFGenerator(
            str(neural["names_neural_path"]), device=device, dtype=str(neural["names_neural_dtype"])
        )
        context = {
            "model": neural["names_neural_model"],
            "dtype": neural["names_neural_dtype"],
            "library": neural["names_neural_library"],
            "decoding": "greedy",  # HFGenerator: do_sample=False
            "max_new_tokens": names_neural.MAX_NEW_TOKENS,
            "n_candidates": names_neural.N_CANDIDATES,
            "batch_size": names_neural.BATCH_SIZE,
            "gpu": torch.cuda.get_device_name(torch.device(device)),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        }
        self.cache = DiskCachedGenerator(generator, str(neural["names_neural_cache"]), context)
        return names_neural.NeuralProposer(
            model=str(neural["names_neural_path"]), generate=self.cache
        )

    def _check(self, item: WorkerItem) -> str:
        if item.expected_message is None:
            raise ValueError(f"{self.method}: item {item.item_id} has no expected_message")
        return str(item.expected_message)

    def embed(self, item: WorkerItem) -> List[WorkerResult]:
        message = self._check(item)
        before = self._neural_counts()
        start = time.perf_counter()
        res = self._embed(item.code or "", message, self.key, self.cfg, self.proposer)
        elapsed = time.perf_counter() - start
        extra: Dict[str, Any] = dict(res.extra)
        extra["reason"] = res.reason
        if self.cache is not None:
            after = self._neural_counts()
            names = ("n_neural_prompts", "n_neural_batches_cached", "n_neural_batches_generated")
            for name, b, a in zip(names, before, after):
                extra[name] = a - b
        status = EMBED_STATUS[res.status]
        return [
            self.result(
                item,
                status,
                code=res.code,
                extra=extra,
                error=res.reason if status == EmbedStatus.FAILED else None,
                elapsed_s=elapsed,
            )
        ]

    def _neural_counts(self) -> List[int]:
        """Prompt chiesti al generatore, lotti letti dalla cache su disco e lotti generati."""
        if self.cache is None:
            return [0, 0, 0]
        return [int(self.proposer.n_generated), self.cache.hits, self.cache.misses]

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
