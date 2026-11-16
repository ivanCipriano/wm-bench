"""Smoke test locale di ``mcss`` e ``multimark`` (PIPELINE_metodo.md di multimark, P2 passo 4).

Catena delle fixture ``tests/fixtures/tiny`` con ``paths=local``: ``generate_baseline`` (backend
finto con codice sintetico della libreria), ``watermark`` (lo shim **reale** ``bench_shims.<m>``
in un processo worker, con la libreria del submodule nel ``PYTHONPATH``), ``execute`` (sandbox
finta), ``detect`` (positivi, negativi umani, negativi LLM), ``calibrate``, ``metrics``.

Si verificano: I1 (una riga per campione), stati di inserimento (``OK``, ``PARTIAL``, ``FAILED``
con ``no_sites``), messaggio atteso nei marcati, codice dello shim anche per ``PARTIAL``, decodifica
dei positivi ``OK``, ``bits_correct`` da ``extra`` (ADR-017 §1), metriche di capacità a 24 bit
(§2), punteggi secondari e preservazione condizionata (§4).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest

import bench.pipeline.stages.generate_baseline as gb
import bench.pipeline.stages.watermark as wm
from bench.config.schema import DecodingConfig, ExperimentConfig, ModelSpec
from bench.execution import sandbox as sb
from bench.methods.adapters.multimark import PosthocMultibitAdapter
from bench.methods.base import MethodAdapter
from bench.methods.worker_client import MethodSource, WorkerClient
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stages.detect import DetectStage, detection_ref
from bench.pipeline.stages.metrics import metrics_ref
from tests.conftest import REPO_ROOT, rebuild
from tests.datafix import build_tiny_datasets, tiny_config
from tests.unit.test_execute import FakeSandbox
from tests.unit.test_inventory_scripts import LIBRARY, needs_library

MODEL = "qwen25_coder_7b"
METHODS = ("mcss", "multimark")


class SyntheticBackend:
    """Baseline finta: codice sintetico della libreria (molti siti, corto o vuoto a rotazione)."""

    calls: ClassVar[list[str]] = []

    def __init__(self, model: ModelSpec, decoding: DecodingConfig) -> None:
        self.decoding = decoding

    def generate(self, messages: list[dict[str, str]], n: int, seed: int) -> list[str]:
        sys.path.insert(0, str(LIBRARY))
        try:
            from multimark.synthetic import generate
        finally:
            sys.path.remove(str(LIBRARY))
        match = re.search(r"call `([^`]+)`", messages[1]["content"])
        entry = match.group(1) if match else "main"
        SyntheticBackend.calls.append(entry)
        outs = []
        for i in range(n):
            kind = (seed + i) % 3
            if kind == 0:  # lungo: N ≥ B = 32
                code = generate(seed + i, n_statements=40, n_functions=2)
            elif kind == 1:  # corto: 0 < N < B
                code = generate(seed + i, n_statements=4)
            else:  # nessun sito
                code = f"def {entry}():\n    return {i}\n"  # niente parametri: mcss li conta
            outs.append(f"```python\n{code}```")
        return outs

    def info(self) -> dict[str, Any]:
        return {"generation_config": {"top_k": 0}, "device": "fake"}


def posthoc_client(cfg: ExperimentConfig, timeout_s: float) -> WorkerClient:
    """Client reale con l'interprete dei test come ambiente ``posthoc``."""
    return WorkerClient(
        envs={"posthoc": Path(sys.executable)},
        shims_root=REPO_ROOT / "shims",
        timeout_s=timeout_s,
        extra_pythonpath=[REPO_ROOT / "packages" / "bench-contracts"],
    )


@pytest.fixture
def facade(cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch) -> BenchmarkFacade:
    build_tiny_datasets(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    BenchmarkFacade(tcfg).run_stage("prepare_data")
    tcfg = rebuild(tcfg, methods=list(METHODS), models=[MODEL], languages=["python"],
                   splits=["dev"])  # fmt: skip
    sources = ["canonical", "llm_baseline", "llm_watermarked"]
    tcfg = rebuild(tcfg, execution={**tcfg.execution.model_dump(), "sources": sources})
    SyntheticBackend.calls = []
    FakeSandbox.jobs, FakeSandbox.files = [], []
    FakeSandbox.fail_after, FakeSandbox.broken = None, False
    monkeypatch.setattr(gb, "BACKEND_FACTORY", SyntheticBackend)
    monkeypatch.setattr(wm, "WORKER_FACTORY", posthoc_client)
    monkeypatch.setattr(sb, "make_sandbox", lambda config: FakeSandbox())
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)
    monkeypatch.setattr(
        PosthocMultibitAdapter, "source", lambda self: MethodSource(LIBRARY, LIBRARY)
    )
    return BenchmarkFacade(tcfg)


def _check_capacity(
    facade: BenchmarkFacade, method: str, positives: pd.DataFrame, marked: pd.DataFrame
) -> None:
    """``scripts/report_capacity.py`` (§5.6, riga di comando) sulla stessa catena."""
    from tests.unit.test_inventory_scripts import _load

    script = _load("report_capacity")
    out = facade.cfg.paths.artifacts.parent / f"capacity_{method}"
    report = out / "capacity.txt"
    out.mkdir(parents=True, exist_ok=True)
    argv = ["--method", method, "--models", MODEL, "--out-dir", str(out), "--report",
            str(report), "paths=local"]  # fmt: skip
    assert script.main(argv) == 0
    sites = pd.read_csv(out / f"capacity_by_sites_{method}_{MODEL}_L1_python_dev.csv")
    assert list(sites["band"][:6]) == ["1-7", "8-12", "13-19", "20-31", "32-55", "56+"]
    assert int(sites["n"].sum()) == len(positives)  # ogni positivo in una sola riga
    big = sites[sites["band"].isin(["32-55", "56+"])]
    n_big = int(big["n"].sum())
    assert n_big == int((marked["embed_status"] == "OK").sum())
    # con N ≥ B tutti i messaggi giusti (positivi OK decodificati)
    assert (big[big["n"] > 0]["message_accuracy"] == 1.0).all()
    assert (out / f"capacity_by_loc_{method}_{MODEL}_L1_python_dev.csv").is_file()
    assert "by n_sites:" in report.read_text(encoding="utf-8")
    assert script.main(argv) == 1  # il riepilogo esiste già


@needs_library
def test_watermark_execute_detect_calibrate_metrics(facade: BenchmarkFacade) -> None:
    for stage in ("generate_baseline", "watermark", "evalplus_groundtruth", "execute", "detect",
                  "calibrate", "metrics"):  # fmt: skip
        report = facade.run_stage(stage)
        assert report.ran >= 1, stage
    baseline = facade.store.read_table(gb.baseline_ref(MODEL, "L1", "python", "dev"))
    for method in METHODS:
        cfg_hash = DetectStage(facade.cfg).config_hash(method)
        marked = facade.store.read_table(
            wm.watermarked_ref(method, MODEL, cfg_hash, "L1", "python", "dev")
        )
        assert len(marked) == len(baseline)  # I1
        statuses = set(marked["embed_status"])
        assert {"OK", "PARTIAL", "FAILED"} <= statuses, (method, statuses)
        assert marked["expected_message"].str.fullmatch(r"[01]{24}").all()
        parents = dict(zip(baseline["sample_id"], baseline["code"], strict=True))
        for row in marked.to_dict(orient="records"):
            parent_code = parents[row["parent_id"]]
            if row["embed_status"] == "FAILED":
                assert row["code"] == parent_code
            if row["embed_status"] == "OK":
                assert row["n_sites"] >= 32
            if row["embed_status"] == "PARTIAL":
                assert 0 < row["n_sites"] < 32

        def det(subject: str, h: str = cfg_hash, m: str = method) -> pd.DataFrame:
            return facade.store.read_table(
                detection_ref(m, MODEL, h, subject, "L1", "python", "dev")
            )

        positives = det("positives")
        assert len(positives) == len(marked)
        ok = positives[positives["embed_status"] == "OK"]
        assert (ok["status"] == "OK").all() and (ok["bits_correct"] == 24).all()
        extras = [json.loads(e) for e in ok["extra_json"]]
        assert all(e["decoded_message_open"] is not None for e in extras)
        assert {"score_nreg16", "best_nreg16", "score_decode", "sites"} <= set(extras[0])
        failed = positives[positives["embed_status"] == "FAILED"]
        assert (failed["status"] == "FAILED").all()  # nessun sito anche in rilevazione
        assert len(det("neg_human")) > 0 and len(det("neg_llm")) == len(baseline)

        metrics = facade.store.read_table(
            metrics_ref(method, MODEL, cfg_hash, "L1", "python", "dev")
        )
        values = dict(zip(metrics["name"], metrics["value"], strict=True))
        expected = {"tpr_at_fpr", "fpr_human", "fpr_llm", "auroc", "pass_at_1",
                    "delta_pass_at_1_pp", "conditional_preservation", "message_accuracy",
                    "bit_accuracy", "embedding_success_rate", "tpr_at_fpr_nreg16",
                    "tpr_at_fpr_nreg1024", "tpr_at_fpr_decode", "auroc_nreg16"}  # fmt: skip
        assert expected <= set(values), expected - set(values)
        n_ok = int((positives["bits_correct"] == 24).sum())
        assert values["message_accuracy"] == pytest.approx(n_ok / len(positives))
        assert values["embedding_success_rate"] == pytest.approx(
            (marked["embed_status"] == "OK").mean()
        )

        _check_capacity(facade, method, positives, marked)
