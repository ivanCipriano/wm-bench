"""Ciclo di vita del worker (SPEC §8.2). Compatibile con Python 3.9.

1. legge e valida la richiesta (``schema_version`` diversa: exit code 3);
2. ``setup`` una volta (errore fatale: exit code 2);
3. **ripresa**: tiene in ``output_path`` solo gli item completi e salta quelli;
4. per ogni item l'operazione è dentro ``try/except``: un'eccezione produce risultati
   ``FAILED`` con ``error`` e non interrompe il ciclo;
5. ogni risultato è una riga JSONL scritta con ``flush`` + ``fsync``;
6. exit code 0 se il ciclo si completa (anche con item falliti).

``--introspect`` stampa su stdout le versioni dell'ambiente (SPEC §8.3).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Dict, List, Optional, Type

from bench_contracts import (
    ContractError,
    WorkerItem,
    WorkerRequest,
    WorkerResult,
    append_jsonl,
    iter_jsonl,
    write_jsonl,
)

from bench_shims._common.base import ShimBase
from bench_shims._common.introspect import introspect

EXIT_OK = 0
EXIT_SETUP = 2
EXIT_VERSION = 3

logger = logging.getLogger("bench_shims")


def expected_results(request: WorkerRequest, item: WorkerItem) -> int:
    """Risultati attesi per un item: ``n`` per l'inserimento da prompt, altrimenti uno."""
    if request.op == "embed" and item.prompt_messages is not None:
        return int(item.n)
    return 1


def _complete_items(
    request: WorkerRequest, items: Sequence[WorkerItem]
) -> Dict[str, List[Dict[str, Any]]]:
    """Righe già scritte degli item completi; riscrive il file senza gli item incompleti."""
    path = request.output_path
    if not os.path.exists(path):
        return {}
    rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in iter_jsonl(path):
        rows[str(row["item_id"])].append(row)
    wanted = {it.item_id: expected_results(request, it) for it in items}
    complete = {k: v for k, v in rows.items() if k in wanted and len(v) >= wanted[k]}
    if len(complete) != len(rows):
        # Un item scritto a metà (worker ucciso tra due righe) si rifà da capo.
        dropped = sorted(set(rows) - set(complete))
        logger.warning("resume: dropping %d incomplete item(s): %s", len(dropped), dropped[:5])
        tmp = path + ".tmp"
        write_jsonl(tmp, [r for k in complete for r in complete[k]])
        os.replace(tmp, path)
    return complete


def run(shim: ShimBase, request: WorkerRequest) -> int:
    """Elabora tutti gli item della richiesta con lo shim già configurato."""
    items = [WorkerItem.from_dict(row) for row in iter_jsonl(request.items_path)]
    done = _complete_items(request, items)
    todo = [it for it in items if it.item_id not in done]
    logger.info("%s %s: %d items, %d already done", shim.method, request.op, len(items), len(done))
    if request.op not in ("embed", "detect"):
        raise ContractError(f"unknown op {request.op!r}")
    size = max(1, int(getattr(shim, "batch_size", 1)))
    done_count = 0
    for begin in range(0, len(todo), size):
        batch = todo[begin : begin + size]
        start = time.perf_counter()
        outputs = (
            _run_batch(shim, request, batch) if size > 1 else [_run_one(shim, request, batch[0])]
        )
        elapsed = time.perf_counter() - start
        for results in outputs:
            for result in results:
                if not result.elapsed_s:
                    result.elapsed_s = elapsed / max(1, len(batch) * len(results))
                append_jsonl(request.output_path, result.to_dict())
        before, done_count = done_count, done_count + len(batch)
        if done_count // 10 > before // 10 or done_count == len(todo):
            logger.info("%d/%d items done", done_count, len(todo))
    return EXIT_OK


def _run_one(shim: ShimBase, request: WorkerRequest, item: WorkerItem) -> List[WorkerResult]:
    """Un item; un'eccezione dà risultati ``FAILED`` senza fermare il worker (SPEC §8.2)."""
    expected = expected_results(request, item)
    try:
        results = list(shim.embed(item)) if request.op == "embed" else [shim.detect(item)]
        if len(results) != expected:
            raise RuntimeError(f"{len(results)} results, expected {expected}")
    except Exception:
        error = traceback.format_exc()
        logger.error("item %s failed: %s", item.item_id, error.strip().splitlines()[-1])
        results = ShimBase.failed(item, request.op, expected, error)
    return results


def _run_batch(
    shim: ShimBase, request: WorkerRequest, batch: List[WorkerItem]
) -> List[List[WorkerResult]]:
    """Un lotto; un'eccezione dà risultati ``FAILED`` per tutti gli item del lotto."""
    try:
        if request.op == "embed":
            outputs = [list(r) for r in shim.embed_batch(batch)]
        else:
            outputs = [[r] for r in shim.detect_batch(batch)]
        if len(outputs) != len(batch):
            raise RuntimeError(f"{len(outputs)} item results, expected {len(batch)}")
        for item, results in zip(batch, outputs):
            if len(results) != expected_results(request, item):
                raise RuntimeError(f"item {item.item_id}: {len(results)} results")
    except Exception:
        error = traceback.format_exc()
        logger.error("batch of %d failed: %s", len(batch), error.strip().splitlines()[-1])
        outputs = [
            ShimBase.failed(item, request.op, expected_results(request, item), error)
            for item in batch
        ]
    return outputs


def main(shim_cls: Type[ShimBase], argv: Optional[Sequence[str]] = None) -> int:
    """Punto d'ingresso di ``python -m bench_shims.<metodo>``."""
    parser = argparse.ArgumentParser(prog=f"bench_shims.{shim_cls.method}")
    parser.add_argument("--request", help="path of request.json")
    parser.add_argument("--introspect", action="store_true", help="print environment versions")
    ns = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if ns.introspect:
        sys.stdout.write(json.dumps(introspect(shim_cls.method), sort_keys=True) + "\n")
        return EXIT_OK
    if not ns.request:
        parser.error("--request is required")
    try:
        with open(ns.request, encoding="utf-8") as handle:
            request = WorkerRequest.from_dict(json.load(handle))
        request.check_version()
    except (ContractError, ValueError, OSError) as exc:
        logger.error("invalid request: %s", exc)
        return EXIT_VERSION
    shim = shim_cls()
    try:
        shim.setup(request)
    except Exception:
        logger.error("setup failed:\n%s", traceback.format_exc())
        return EXIT_SETUP
    return run(shim, request)


__all__ = [
    "EXIT_OK",
    "EXIT_SETUP",
    "EXIT_VERSION",
    "WorkerResult",
    "expected_results",
    "main",
    "run",
]
