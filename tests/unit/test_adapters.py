"""Adapter della Milestone 6 e rilevazione generica (``Detector.detect_codes``) senza GPU."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.methods.adapters.stone import StoneAdapter
from bench.methods.adapters.sweet import SweetAdapter
from bench.methods.base import DetectInput, MethodAdapter
from bench.methods.worker_client import WorkerClient, WorkerRun
from tests.conftest import REPO_ROOT


class EchoClient(WorkerClient):
    """Client reale che lancia lo shim ``echo`` con l'interprete dei test."""

    def run(
        self,
        env_name: str,
        method: str,
        request: Any,
        items: Any,
        source: Any = None,
        secrets: Any = (),
    ) -> WorkerRun:  # type: ignore[override]
        self.last_items = list(items)
        return super().run("test", "echo", request, items, None, secrets)


def echo_client() -> EchoClient:
    return EchoClient(
        envs={"test": Path(sys.executable)},
        shims_root=REPO_ROOT / "shims",
        timeout_s=60,
        extra_pythonpath=[REPO_ROOT / "packages" / "bench-contracts"],
    )


@pytest.fixture(autouse=True)
def fake_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)


def test_sweet_hparams(cfg: ExperimentConfig) -> None:
    a = SweetAdapter(cfg.methods_catalog["sweet"], cfg, echo_client())
    hp = a.default_hparams()
    assert hp == {"gamma": 0.5, "delta": 0.5, "entropy_threshold": 0.5}
    assert a.to_native_hparams(hp) == {
        "gamma": 0.5,
        "delta": 0.5,
        "entropy_threshold": 0.5,
        "seeding_scheme": "simple_1",
        "select_green_tokens": True,
        "z_threshold": 4.0,
    }
    with pytest.raises(ConfigError, match="missing"):
        a.to_native_hparams({"gamma": 0.5, "delta": 0.5})
    with pytest.raises(ConfigError, match="unknown"):
        a.to_native_hparams({**hp, "hash_key": 1})
    with pytest.raises(ConfigError, match="entropy_threshold"):
        a.to_native_hparams({**hp, "entropy_threshold": -1})
    assert all(a.supports(lang) for lang in ("python", "java", "cpp", "javascript"))
    assert a.gpu_for_detect and a.source().pythonpath == a.source().root
    assert a.key("k1") != StoneAdapter(cfg.methods_catalog["stone"], cfg, echo_client()).key("k1")


def test_detect_codes_keeps_order_and_skips_unsupported_languages(
    cfg: ExperimentConfig, tmp_path: Path
) -> None:
    client = echo_client()
    a = StoneAdapter(cfg.methods_catalog["stone"], cfg, client)
    model = cfg.models_catalog["qwen25_coder_7b"]
    inputs = [
        DetectInput(
            "s/1",
            "python",
            "def f():\n    return 1\n",
            prompt_messages=[{"role": "user", "content": "x"}],
        ),
        DetectInput("s/2", "javascript", "const f = () => 1"),
        DetectInput("s/3", "cpp", "int f(){return 1;}", context_prompt="ctx"),
    ]
    results = a.detect_codes(inputs, a.default_hparams(), model, "k1", {}, tmp_path, "cpu")
    assert [r.item_id for r in results] == ["s/1", "s/2", "s/3"]
    assert [r.status for r in results] == ["OK", "NOT_APPLICABLE", "OK"]
    assert results[0].score == float(len(inputs[0].code)) and results[1].score is None
    # Solo i linguaggi supportati arrivano al worker, con il loro contesto.
    assert [it.item_id for it in client.last_items] == ["s/1", "s/3"]
    assert client.last_items[0].prompt_messages == [{"role": "user", "content": "x"}]
    assert client.last_items[1].context_prompt == "ctx"
