"""``WorkerClient`` e runner degli shim con lo shim di prova ``echo`` (SPEC §8; ADR-008).

Il worker è un vero sottoprocesso (``python -m bench_shims.echo``) con l'interprete dei test:
si verificano ripresa dopo l'uccisione, timeout con un solo rilancio, codici di uscita,
risultati mancanti (I1) e ambiente ripulito.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from bench_contracts import SCHEMA_VERSION, WorkerItem, WorkerRequest, WorkerResult, write_jsonl

from bench.domain.errors import WorkerError
from bench.methods.worker_client import MISSING_RESULT, MethodSource, WorkerClient
from tests.conftest import REPO_ROOT

SHIMS = REPO_ROOT / "shims"
CONTRACTS = REPO_ROOT / "packages" / "bench-contracts"


def client(timeout_s: float = 60, **kwargs: Any) -> WorkerClient:
    return WorkerClient(
        envs={"test": Path(sys.executable)},
        shims_root=SHIMS,
        timeout_s=timeout_s,
        extra_pythonpath=[CONTRACTS],
        **kwargs,
    )


def request(run_dir: Path, op: str = "embed", **hparams: Any) -> WorkerRequest:
    return WorkerRequest(
        schema_version=SCHEMA_VERSION,
        op=op,
        method="echo",
        model_id="m",
        model_path="/none",
        tokenizer_path="/none",
        hparams=hparams,
        key=1,
        key_id="k1",
        decoding={"temperature": 0.2},
        system_prompt="sys",
        items_path=str(run_dir / "items.jsonl"),
        output_path=str(run_dir / "results.jsonl"),
        device="cpu",
        log_path=str(run_dir / "worker.log"),
    )


def items(k: int, n: int = 2, prompt: bool = True) -> list[WorkerItem]:
    return [
        WorkerItem(
            item_id=f"p/{i}",
            language="python",
            seed=100 + i,
            prompt_messages=[{"role": "user", "content": f"task {i}"}] if prompt else None,
            code=None if prompt else f"code {i}",
            context_prompt=None,
            expected_message=None,
            n=n,
        )
        for i in range(k)
    ]


def test_embed_returns_n_results_per_item_in_order(tmp_path: Path) -> None:
    run = client().run("test", "echo", request(tmp_path), items(3, n=2))
    assert [(r.item_id, r.sample_index) for r in run.results] == [
        (f"p/{i}", j) for i in range(3) for j in range(2)
    ]
    assert {r.status for r in run.results} == {"OK"} and run.missing == 0 and run.attempts == 1
    assert run.results[0].raw_output == "```\ntask 0\n```"
    assert run.results[0].extra == {"seed": 100}


def test_detect_returns_one_result_per_item(tmp_path: Path) -> None:
    run = client().run("test", "echo", request(tmp_path, op="detect"), items(2, prompt=False))
    assert [(r.item_id, r.sample_index, r.score) for r in run.results] == [
        ("p/0", None, 6.0),
        ("p/1", None, 6.0),
    ]


def test_resume_after_the_worker_is_killed(tmp_path: Path) -> None:
    markers = tmp_path / "markers"
    markers.mkdir()
    # 1 s per item, timeout 2,5 s: il primo tentativo viene ucciso dopo circa 2 item,
    # il secondo riprende senza rifare quelli già scritti.
    req = request(tmp_path, sleep_s=1.0, marker_dir=str(markers))
    run = client(timeout_s=2.5).run("test", "echo", req, items(4, n=2))
    assert run.attempts == 2
    assert [(r.item_id, r.sample_index) for r in run.results] == [
        (f"p/{i}", j) for i in range(4) for j in range(2)
    ]
    assert {r.status for r in run.results} == {"OK"}
    lines = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    keys = [(json.loads(x)["item_id"], json.loads(x)["sample_index"]) for x in lines]
    assert len(keys) == len(set(keys)) == 8  # nessun duplicato
    started = sorted(p.name.split(".")[0] for p in markers.iterdir())
    # Al più l'item in corso al momento dell'uccisione viene rifatto.
    assert 4 <= len(started) <= 5
    assert "timeout after" in (tmp_path / "worker.log").read_text(encoding="utf-8")


def test_two_timeouts_raise_with_the_log_tail(tmp_path: Path) -> None:
    req = request(tmp_path, sleep_s=5.0)
    with pytest.raises(WorkerError, match="failed twice") as info:
        client(timeout_s=1.0).run("test", "echo", req, items(3))
    assert "=====" in str(info.value)


def test_setup_failure_is_fatal_without_retry(tmp_path: Path) -> None:
    with pytest.raises(WorkerError, match="exit code 2") as info:
        client().run("test", "echo", request(tmp_path, setup_error=True), items(1))
    assert "simulated setup failure" in str(info.value)
    assert (tmp_path / "worker.log").read_text(encoding="utf-8").count("===== ") == 1


def test_wrong_schema_version_exits_with_3(tmp_path: Path) -> None:
    req = request(tmp_path)
    req.schema_version = "0.9"
    with pytest.raises(WorkerError, match="exit code 3"):
        client().run("test", "echo", req, items(1))


def test_item_failures_do_not_stop_the_worker(tmp_path: Path) -> None:
    req = request(tmp_path, fail_items=["p/1"], drop_items=["p/2"])
    run = client().run("test", "echo", req, items(4, n=2))
    by_item = {(r.item_id, r.sample_index): r for r in run.results}
    assert by_item[("p/1", 0)].status == by_item[("p/1", 1)].status == "FAILED"
    assert "simulated failure on p/1" in (by_item[("p/1", 0)].error or "")
    # Un risultato in meno è un errore dell'item (I1), non una riga mancante.
    assert by_item[("p/2", 1)].status == "FAILED" and "expected 2" in (
        by_item[("p/2", 1)].error or ""
    )
    assert by_item[("p/3", 1)].status == "OK"


def test_missing_results_are_filled_as_failed(tmp_path: Path) -> None:
    req = request(tmp_path)
    its = items(2, n=2)
    present = WorkerResult(
        item_id="p/0",
        sample_index=0,
        status="OK",
        raw_output="x",
        code=None,
        score=None,
        native_decision=None,
        decoded_message=None,
    )
    write_jsonl(tmp_path / "results.jsonl", [present.to_dict()])
    seen: list[tuple[str, int]] = []
    run = client(on_missing=lambda m, k: seen.append((m, k))).collect(req, its)
    assert [r.status for r in run.results] == ["OK", "FAILED", "FAILED", "FAILED"]
    assert run.results[1].error == MISSING_RESULT and run.missing == 3


def test_environment_is_cleaned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTHONPATH", "/somewhere/bench-core")
    monkeypatch.setenv("SOURCERY_TOKEN", "secret")
    monkeypatch.setenv("WANDB_API_KEY", "x")
    source = MethodSource(
        pythonpath=tmp_path / "patched" / "m" / "sub", root=tmp_path / "patched" / "m"
    )
    env = client().environment(source)
    paths = env["PYTHONPATH"].split(__import__("os").pathsep)
    assert paths[0] == str(SHIMS) and paths[1] == str(source.pythonpath)
    assert "/somewhere/bench-core" not in env["PYTHONPATH"]
    assert env["WMB_METHOD_SOURCE"] == str(source.root)
    assert env["TRANSFORMERS_OFFLINE"] == "1" and env["PYTHONHASHSEED"] == "0"
    assert "SOURCERY_TOKEN" not in env and "WANDB_API_KEY" not in env
    assert client().environment(source, secrets=("SOURCERY_TOKEN",))["SOURCERY_TOKEN"] == "secret"


def test_introspect(tmp_path: Path) -> None:
    root = tmp_path / "patched" / "echo"
    root.mkdir(parents=True)
    Path(f"{root}.source_commit").write_text("abc123\n", encoding="utf-8")
    info = client().introspect("test", "echo", MethodSource(root, root), timeout_s=300)
    assert info["method"] == "echo" and info["source_commit"] == "abc123"
    assert info["python"] and info["packages_sha256"]
