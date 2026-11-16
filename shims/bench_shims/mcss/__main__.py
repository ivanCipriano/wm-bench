"""Punto d'ingresso dello shim di ``mcss`` (``python -m bench_shims.mcss``). Compatibile con
Python 3.9."""

from __future__ import annotations

from bench_shims._common.runner import main
from bench_shims.multimark.shim_base import PosthocShim


class McssShim(PosthocShim):
    """Reimplementazione di MCSS (profilo mcss negli iperparametri dell'adapter, §3.11, §4).
    ``method = "mcss"``: esche ``mcss-registry`` e ``native_decision`` di §4.2 punto 5."""

    method = "mcss"


if __name__ == "__main__":
    raise SystemExit(main(McssShim))
