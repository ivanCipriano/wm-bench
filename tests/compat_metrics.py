"""Metriche di riferimento per la retrocompatibilità delle modifiche comuni di P2 (ADR-017).

PIPELINE_metodo.md di multimark, §5.1–§5.4: le modifiche a ``detect.py`` e ``metrics.py`` non devono
cambiare le metriche dei metodi esistenti. Questo modulo fa girare la catena finta dei test
(``tests/unit/test_detection_chain.py``: shim ``echo``, sandbox finta, fixture ``tiny``) per:

- MCGMark (multi-bit, baseline gemella): ``detect → calibrate → metrics`` senza attacco;
- MCGMark sotto T1.5 (tre punti della griglia): ``attack → execute → detect → metrics``;
- STONE (punteggio principale da ``extra``, D16): ``watermark → … → metrics``;
- ACW (``CodeEmbedder``, ``conditional_preservation``): ``watermark → … → metrics``.

e raccoglie tutte le righe dei parquet ``metrics/`` (nome, valore, intervallo, ``n``).

``python -m tests.compat_metrics`` scrive ``tests/fixtures/compat/metrics_before_p2.json`` con il
codice **di prima** delle modifiche comuni (commit indicato nel file); il test
``tests/unit/test_metrics_compat.py`` rifà il calcolo con il codice attuale e confronta riga per
riga a tolleranza 1e-9. Un file esistente non si sovrascrive.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "compat" / "metrics_before_p2.json"
MODEL = "qwen25_coder_7b"
METRIC_FIELDS = ("value", "ci_low", "ci_high", "n", "ci_method")


def _rows(store_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """``percorso relativo → nome della metrica → campi`` per tutti i parquet di ``metrics*/``."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(store_root.glob("metrics*/**/*.parquet")):
        table = pd.read_parquet(path)
        rel = path.relative_to(store_root).as_posix()
        out[rel] = {}
        for rec in table.to_dict(orient="records"):
            fields: dict[str, Any] = {}
            for f in METRIC_FIELDS:
                v = rec[f]
                if f in ("value", "ci_low", "ci_high"):
                    v = None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
                elif f == "n":
                    v = None if v is None or pd.isna(v) else int(v)
                fields[f] = v
            out[rel][str(rec["name"])] = fields
    return out


def compute(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Fa girare le quattro catene sotto ``root`` e restituisce le righe delle metriche."""
    import bench.pipeline.stages.generate_baseline as gb
    import bench.pipeline.stages.watermark as wm
    from bench.config.builder import load_experiment
    from bench.execution import sandbox as sb
    from bench.methods.base import MethodAdapter
    from bench.pipeline.facade import BenchmarkFacade
    from tests.conftest import rebuild
    from tests.datafix import build_tiny_datasets, tiny_config
    from tests.unit.test_execute import FakeSandbox
    from tests.unit.test_generate_baseline import FakeBackend
    from tests.unit.test_watermark import echo_client

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WMB_LOCAL_ROOT", str(root))
        mp.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
        cfg = load_experiment(["paths=local", "stage=selftest"])
        build_tiny_datasets(cfg.paths.datasets)
        tcfg = tiny_config(cfg)
        BenchmarkFacade(tcfg).run_stage("prepare_data")
        FakeBackend.calls, FakeBackend.fail_after = [], None
        FakeSandbox.jobs, FakeSandbox.files = [], []
        FakeSandbox.fail_after, FakeSandbox.broken = None, False
        mp.setattr(gb, "BACKEND_FACTORY", FakeBackend)
        mp.setattr(wm, "WORKER_FACTORY", echo_client)
        mp.setattr(sb, "make_sandbox", lambda config: FakeSandbox())
        mp.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)

        def config(method: str, sources: list[str], attacks: list[str]) -> Any:
            c = rebuild(tcfg, methods=[method], models=[MODEL], languages=["python"],
                        splits=["dev"], attacks=attacks)  # fmt: skip
            return rebuild(c, execution={**c.execution.model_dump(), "sources": sources})

        # MCGMark senza attacco
        mcg = config("mcgmark", ["canonical", "llm_baseline", "llm_watermarked", "baseline_twin"],
                     [])  # fmt: skip
        facade = BenchmarkFacade(mcg)
        for stage in (
            "generate_baseline",
            "watermark",
            "generate_baseline_twin",
            "evalplus_groundtruth",
            "execute",
            "detect",
            "calibrate",
            "metrics",
        ):
            facade.run_stage(stage)  # fmt: skip
        # MCGMark sotto T1.5 (stessa soglia congelata della cella senza attacco)
        attacked = BenchmarkFacade(config("mcgmark", ["attacked"], ["T1.5"]))
        for stage in ("attack", "execute", "detect", "metrics"):
            attacked.run_stage(stage)
        # STONE (punteggio principale da ``extra``, D16): un solo bit, nessuna capacità
        stone = BenchmarkFacade(config("stone", ["llm_watermarked"], []))
        for stage in ("watermark", "execute", "detect", "calibrate", "metrics"):
            stone.run_stage(stage)
        # ACW (CodeEmbedder): la baseline c'è già
        acw = BenchmarkFacade(config("acw", ["llm_watermarked"], []))
        for stage in ("watermark", "execute", "detect", "calibrate", "metrics"):
            acw.run_stage(stage)
        return _rows(Path(mcg.paths.artifacts))


def main() -> int:
    if GOLDEN.exists():
        print(f"{GOLDEN} exists: refusing to overwrite", file=sys.stderr)
        return 1
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "src"], capture_output=True, text=True, check=True
    ).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        rows = compute(Path(tmp) / "wmb")
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": "metrics of existing methods before the P2 common changes (ADR-017)",
        "wm_bench_commit": commit,
        "src_dirty": bool(dirty),
        "cells": rows,
    }
    GOLDEN.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    n = sum(len(v) for v in rows.values())
    print(f"wrote {GOLDEN}: {len(rows)} cells, {n} metric rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
