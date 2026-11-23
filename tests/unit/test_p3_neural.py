"""Proponente neurale di multimark nel benchmark (P3, MD31).

- cache su disco dello shim (``bench_shims.multimark.neural_cache``): chiave con lotto, modello,
  prompt e parametri di generazione, nessun riuso se uno di questi cambia;
- piano dell'inserimento di ``scripts/run_p2_p3.sh``: un solo job GPU in cui ogni cella di D segue
  quella di B (stesso modello e livello);
- shim con il proponente neurale e un modello finto (niente ``torch``): ripiego contato negli
  ``extra``, cache scritta da B e letta da D con lo stesso risultato, GPU obbligatoria, rilevazione
  senza modello.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

import pytest
from bench_contracts import WorkerItem

from bench.config.schema import ExperimentConfig
from bench.methods.adapters.multimark import message_bits
from bench.methods.base import MethodAdapter
from bench.pipeline.stages.watermark import make_adapter
from tests.conftest import REPO_ROOT
from tests.unit.test_inventory_scripts import LIBRARY

SHIMS = REPO_ROOT / "shims"
B = {"names.bits_per_name": 2, "names.proposer": "neural"}
D = {"names.single_letter": "extend_except_loop", "canonical.same_kind": "binding_order",
     "names.bits_per_name": 2, "names.include_params": True,
     "names.proposer": "neural"}  # fmt: skip
needs_neural = pytest.mark.skipif(
    not (LIBRARY / "multimark" / "names_neural.py").is_file(),
    reason="third_party/multimark without names_neural.py (library before P3)",
)


@pytest.fixture(autouse=True)
def fake_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)


@pytest.fixture
def shims_path() -> Iterator[None]:
    sys.path.insert(0, str(SHIMS))
    try:
        yield
    finally:
        sys.path.remove(str(SHIMS))


# ---------------------------------------------------------------- cache su disco


def _messages(var: str, system: str = "sys") -> list[dict[str, str]]:
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"x = 1\n\nVariable to rename: {var}"}]  # fmt: skip


def test_disk_cache(tmp_path: Path, shims_path: None) -> None:
    from bench_shims.multimark.neural_cache import DiskCachedGenerator

    calls: list[int] = []

    def gen(batch: Sequence[Any]) -> list[str]:
        calls.append(len(batch))
        return [f'["{m[1]["content"][-1]}_value"]' for m in batch]

    ctx = {"model": "repo@snap", "max_new_tokens": 128, "decoding": "greedy"}
    batch = [_messages("a"), _messages("b")]
    first = DiskCachedGenerator(gen, str(tmp_path), ctx)
    out = first(batch)
    assert out == ['["a_value"]', '["b_value"]'] and (first.hits, first.misses) == (0, 1)
    assert first([]) == [] and calls == [2]
    # un altro worker con lo stesso contesto legge il file, senza generare
    second = DiskCachedGenerator(gen, str(tmp_path), ctx)
    assert second(batch) == out and (second.hits, second.misses) == (1, 0) and calls == [2]
    # lotto diverso (ordine, composizione, prompt) o contesto diverso: nessun riuso
    for other_batch, other_ctx in (
        ([_messages("b"), _messages("a")], ctx),
        ([_messages("a")], ctx),
        ([_messages("a", "sys2"), _messages("b")], ctx),
        (batch, {**ctx, "model": "repo@other"}),
        (batch, {**ctx, "max_new_tokens": 64}),
        (batch, {**ctx, "decoding": "sampling"}),
    ):
        g = DiskCachedGenerator(gen, str(tmp_path), other_ctx)
        g(other_batch)
        assert (g.hits, g.misses) == (0, 1)
    assert len(calls) == 7
    # un file con contenuto diverso dalla chiave (o rovinato) non si usa
    path = first.path(first.key(batch))
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "batch": [_messages("z"), _messages("b")]}),
                    encoding="utf-8")  # fmt: skip
    third = DiskCachedGenerator(gen, str(tmp_path), ctx)
    assert third(batch) == out and third.misses == 1
    path.write_text("{not json", encoding="utf-8")
    assert DiskCachedGenerator(gen, str(tmp_path), ctx)(batch) == out
    assert not list(tmp_path.rglob("*.tmp"))


# ---------------------------------------------------------------- piano dell'inserimento


def test_watermark_plan_is_one_gpu_job_with_d_after_b(
    monkeypatch: pytest.MonkeyPatch, local_root: Path
) -> None:
    """Con la regola di ``run_p2_p3.sh`` le 8 celle vanno su GPU; in un solo job l'ordine è
    (modello, livello) × (B, D), qualunque sia il peso delle celle; senza la regola vanno su CPU.
    La griglia è quella del giro P3 (i soli B e D neurali), non più in ``configs/hpo``."""
    import bench.hpo.grid as grid_mod
    import bench.pipeline.stages  # noqa: F401

    p3_grid = local_root / "hpo_p3" / "multimark.yaml"
    p3_grid.parent.mkdir(parents=True)
    lines = ["method: multimark", "include:", *(f"  - {json.dumps(p)}" for p in (B, D))]
    p3_grid.write_text("\n".join(lines) + "\n", encoding="utf-8")
    real = grid_mod.grid_path

    def p3_path(method: str, root: Path | None = None) -> Path:
        return p3_grid if method == "multimark" and root is None else real(method, root)

    monkeypatch.setattr(grid_mod, "grid_path", p3_path)
    from bench.config.builder import load_experiment
    from bench.config.resources import ResourceClass
    from bench.doctor import load_cluster_profiles
    from bench.pipeline.submit import plan_submission, split_into_lanes

    profiles = load_cluster_profiles(REPO_ROOT / "configs" / "cluster")
    clean = ["paths=local", "stage=watermark", "methods=[multimark]", "hpo.grid=multimark",
             "levels=[L1,L2]", "splits=[dev]", "languages=[python]"]  # fmt: skip
    gpu = "resources.gpu_methods_by_stage.watermark.multimark=true"
    (cpu_group,) = plan_submission(load_experiment(clean), "watermark", profiles)
    assert cpu_group.resource is ResourceClass.CPU
    cfg = load_experiment([*clean, gpu])
    (group,) = plan_submission(cfg, "watermark", profiles)
    assert group.resource is ResourceClass.GPU_NVIDIA and group.profile.partition == "gpuq"
    mm = make_adapter(cfg, "multimark")
    hb, hd = mm.config_hash(B), mm.config_hash(D)
    assert len(group.cells) == 8
    weights = [576 if c.level == "L1" else 300 for c in group.cells]
    (lane,) = split_into_lanes(group.cells, weights, 1)
    assert lane == group.cells
    for first, second in zip(lane[::2], lane[1::2], strict=True):
        assert (first.config_hash, second.config_hash) == (hb, hd)
        assert (first.model_id, first.level) == (second.model_id, second.level)
    assert len({(c.model_id, c.level) for c in lane}) == 4


# ---------------------------------------------------------------- shim con un modello finto

SUFFIXES = ("value", "val", "res", "tmp", "var", "ref", "cur", "new", "prev", "next", "last",
            "orig", "out", "item", "data", "info")  # fmt: skip


class FakeGenerator:
    """Al posto di ``HFGenerator``: 16 nomi ``<var>_<suffisso>`` per variabile, nessuno per le
    variabili che cominciano per ``s`` (per avere ripieghi sui candidati statici)."""

    instances: ClassVar[list[FakeGenerator]] = []

    def __init__(self, model: str, device: str | None = None, dtype: str = "float32") -> None:
        self.model, self.device, self.dtype = model, device, dtype
        self.batches: list[int] = []
        FakeGenerator.instances.append(self)

    def __call__(self, batch: Sequence[Any]) -> list[str]:
        self.batches.append(len(batch))
        out = []
        for messages in batch:
            var = messages[-1]["content"].rsplit("Variable to rename: ", 1)[1].strip()
            names = [] if var.startswith("s") else [f"{var}_{s}" for s in SUFFIXES]
            out.append(json.dumps(names))
        return out


def _fake_torch(cuda: bool) -> types.ModuleType:
    torch = types.ModuleType("torch")
    torch.__version__ = "0.0-fake"  # type: ignore[attr-defined]
    torch.cuda = types.SimpleNamespace(  # type: ignore[attr-defined]
        is_available=lambda: cuda, get_device_name=lambda device=None: "FakeGPU"
    )
    torch.device = lambda name: name  # type: ignore[attr-defined]
    return torch


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    transformers = types.ModuleType("transformers")
    transformers.__version__ = "0.0-fake"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True))
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    sys.path[:0] = [str(LIBRARY), str(SHIMS)]
    try:
        from multimark import names_neural

        monkeypatch.setattr(names_neural, "HFGenerator", FakeGenerator)
        FakeGenerator.instances = []
        yield
    finally:
        sys.path.remove(str(LIBRARY))
        sys.path.remove(str(SHIMS))


def _programs() -> list[str]:
    path = REPO_ROOT / "tests" / "fixtures" / "tiny" / "humanevalplus.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r["prompt"] + r["canonical_solution"] for r in rows]


def _run(cfg: ExperimentConfig, hp: dict[str, Any], op: str = "embed", device: str = "cuda:0",
         cache: Path | None = None) -> tuple[Any, list[Any]]:  # fmt: skip
    from bench_shims.multimark.__main__ import MultimarkShim

    a = make_adapter(cfg, "multimark")
    model = cfg.models_catalog["qwen25_coder_7b"]
    request = a.build_request(op, model, hp, "k1", {}, "", REPO_ROOT, device)
    if cache is not None:
        request = replace(request, hparams={**request.hparams, "names_neural_cache": str(cache)})
    shim = MultimarkShim()
    shim.setup(request)
    if op != "embed":
        return shim, []
    results = []
    for i, code in enumerate(_programs()):
        msg = message_bits(cfg.global_seed, "multimark", f"p{i}", "python", model.model_id, i)
        item = WorkerItem(item_id=f"s{i}", language="python", seed=0, prompt_messages=None,
                          code=code, context_prompt=None, expected_message=msg, n=1)  # fmt: skip
        (res,) = shim.embed(item)
        results.append((item, res))
    return shim, results


NEURAL_EXTRA = ("n_names_neural_fallback", "n_neural_prompts", "n_neural_batches_cached",
                "n_neural_batches_generated")  # fmt: skip


@needs_neural
def test_neural_shim_cache_shared_by_b_and_d(cfg: ExperimentConfig, fake_model: None,
                                             tmp_path: Path) -> None:  # fmt: skip
    a = make_adapter(cfg, "multimark")
    cache = a.neural_cache_dir()
    shim_b, res_b = _run(cfg, B)
    (gen_b,) = FakeGenerator.instances
    assert gen_b.model == str(cfg.models_catalog["qwen25_coder_1p5b"].path)
    assert (gen_b.device, gen_b.dtype) == ("cuda:0", "float32")
    assert shim_b.cache.context["model"].startswith("Qwen/Qwen2.5-Coder-1.5B-Instruct@2e1fd39")
    assert shim_b.cache.context["gpu"] == "FakeGPU"
    named = [r for _, r in res_b if r.extra.get("n_name_sites")]
    assert named and all(all(k in r.extra for k in NEURAL_EXTRA) for _, r in res_b
                         if r.status != "FAILED")  # fmt: skip
    assert sum(r.extra["n_neural_batches_generated"] for _, r in res_b) == len(gen_b.batches) > 0
    assert sum(r.extra["n_neural_batches_cached"] for _, r in res_b) == 0
    assert sum(r.extra["n_names_neural_fallback"] for _, r in res_b) > 0  # variabili con la s
    assert any(r.code != item.code for item, r in res_b)
    assert list(cache.rglob("*.json"))
    # D, un altro worker sugli stessi codici: i codici per cui B ha chiesto il modello vengono
    # dalla cache (B non lo chiede se un nome ha già i bit giusti o se non ha siti di nomi)
    shim_d, res_d = _run(cfg, D)
    assert shim_d.cache.hits > 0
    assert sum(r.extra["n_neural_batches_cached"] for _, r in res_d) == shim_d.cache.hits
    for (_, rb), (_, rd) in zip(res_b, res_d, strict=True):
        if rb.extra.get("n_neural_batches_generated"):
            assert rd.extra.get("n_neural_batches_generated", 0) == 0
    # un altro D: tutto dalla cache
    shim_d2, _ = _run(cfg, D)
    assert FakeGenerator.instances[-1].batches == [] and shim_d2.cache.misses == 0
    # con una cache vuota D dà lo stesso inserimento (la cache non cambia il risultato)
    _, res_fresh = _run(cfg, D, cache=tmp_path / "fresh")
    assert FakeGenerator.instances[-1].batches
    assert [(r.status, r.code) for _, r in res_fresh] == [(r.status, r.code) for _, r in res_d]
    # rilevazione: niente modello; uguale a quella della stessa configurazione con il proponente
    # statico (il bit si legge dal nome)
    det, _ = _run(cfg, D, op="detect")
    assert det.proposer is None and det.cache is None
    det_static, _ = _run(cfg, {**D, "names.proposer": "static"}, op="detect", device="cpu")
    marked = [(item, r) for item, r in res_d if r.status in ("OK", "PARTIAL")]
    assert marked
    for item, r in marked:
        found = det.detect(replace(item, code=r.code))
        same = det_static.detect(replace(item, code=r.code))
        assert (found.status, found.score, found.decoded_message) == (
            same.status, same.score, same.decoded_message)  # fmt: skip
        assert found.status == "OK" and found.score is not None


@needs_neural
def test_neural_shim_needs_a_gpu(cfg: ExperimentConfig, fake_model: None,
                                 monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    with pytest.raises(RuntimeError, match="needs a CUDA GPU"):
        _run(cfg, B, device="cpu")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False))
    with pytest.raises(RuntimeError, match="needs a CUDA GPU"):
        _run(cfg, B)
    assert FakeGenerator.instances == []


@needs_neural
def test_static_shim_has_no_neural_fields(cfg: ExperimentConfig, fake_model: None) -> None:
    shim, res = _run(cfg, {"names.bits_per_name": 2}, device="cpu")
    assert shim.cache is None and FakeGenerator.instances == []
    assert not any(k in r.extra for _, r in res for k in NEURAL_EXTRA)
