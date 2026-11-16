"""Punto d'ingresso dello shim di ``multimark`` (``python -m bench_shims.multimark``).
Compatibile con Python 3.9."""

from __future__ import annotations

from bench_shims._common.runner import main
from bench_shims.multimark.shim_base import PosthocShim


class MultimarkShim(PosthocShim):
    """MultiMark (configurazione negli iperparametri dell'adapter, §3.11)."""

    method = "multimark"


if __name__ == "__main__":
    raise SystemExit(main(MultimarkShim))
