"""Adapter e shim di ``mcss`` e ``multimark`` (PIPELINE_metodo.md di multimark, P2 passi 1–2).

Adapter: profili di §3.11 uguali a ``configs/method/*.yaml``, valori ammessi uguali a quelli della
libreria, validazione delle chiavi, ``config_hash`` senza ``registry_seed``, messaggio di 24 bit,
``bits_correct`` da ``extra``. Shim: ``PosthocShim`` con la libreria del submodule, in processo,
su un frammento sintetico (round trip) e su codice senza siti.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from bench_contracts import WorkerItem, WorkerRequest, WorkerResult

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.ids import config_hash
from bench.methods.adapters.mcss import McssAdapter
from bench.methods.adapters.multimark import (
    CHOICES,
    NATIVE_NAMES,
    MultimarkAdapter,
    PosthocMultibitAdapter,
    flatten,
    message_bits,
)
from bench.methods.base import CodeEmbedder, Detector, MethodAdapter
from bench.pipeline.stages.watermark import make_adapter
from tests.conftest import REPO_ROOT
from tests.unit.test_inventory_scripts import LIBRARY, needs_library

METHODS = ("mcss", "multimark")


@pytest.fixture(autouse=True)
def fake_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)


def _adapter(cfg: ExperimentConfig, method: str) -> PosthocMultibitAdapter:
    adapter = make_adapter(cfg, method)
    assert isinstance(adapter, PosthocMultibitAdapter)
    return adapter


# ---------------------------------------------------------------- adapter


@pytest.mark.parametrize("method", METHODS)
def test_capabilities(cfg: ExperimentConfig, method: str) -> None:
    a = _adapter(cfg, method)
    assert isinstance(a, CodeEmbedder) and isinstance(a, Detector)
    assert (a.gpu_for_embed, a.gpu_for_detect, a.message_bits) == (False, False, 24)
    assert a.secondary_scores == {"nreg16": "score_nreg16", "nreg1024": "score_nreg1024",
                                  "decode": "score_decode"}  # fmt: skip
    assert a.supports("python") and not a.supports("java")
    assert method not in cfg.methods  # nel catalogo, non nella lista di default
    assert a.env_name == "posthoc"
    assert a.source().pythonpath == REPO_ROOT / "build" / "patched" / "multimark"


@pytest.mark.parametrize("method", METHODS)
def test_yaml_profile_is_the_adapter_profile(cfg: ExperimentConfig, method: str) -> None:
    a = _adapter(cfg, method)
    assert flatten(a.default_hparams()) == a.PROFILE
    assert set(a.PROFILE) == set(NATIVE_NAMES)
    assert a.config_hash(a.default_hparams()) == a.config_hash({})


def test_profiles_follow_the_pipeline_table() -> None:
    mm, mc = MultimarkAdapter.PROFILE, McssAdapter.PROFILE
    assert (mm["linking"], mm["mask"], mm["canonical"], mm["names.variant"]) == (
        "function_channel", "on", "kind", "V2")  # fmt: skip
    assert (mm["names.proposer"], mm["names.include_params"], mm["channels"]) == (
        "static", False, "extended")  # fmt: skip
    assert (mm["code.matrix"], mm["names.single_letter"], mm["names.module_level"]) == (
        "dmin4", "exclude", True)  # fmt: skip
    assert (mc["linking"], mc["mask"], mc["canonical"], mc["names.variant"]) == (
        "positional", "off", "paper", "V1")  # fmt: skip
    assert (mc["names.include_params"], mc["channels"], mc["names.module_level"]) == (
        True, "mcss8", True)  # fmt: skip
    assert (mc["code.matrix"], mc["names.single_letter"]) == ("random", "extend_except_loop")


@needs_library
def test_choices_and_names_match_the_library() -> None:
    sys.path.insert(0, str(LIBRARY))
    try:
        from multimark import config as mc
        from multimark.channels import CANONICAL_MODES, CHANNEL_SET_NAMES
        from multimark.codec import MATRICES
        from multimark.linking import LINKING_MODES
        from multimark.names import NAME_VARIANTS, SINGLE_LETTER_MODES
    finally:
        sys.path.remove(str(LIBRARY))
    assert CHOICES["t"] == mc.T_VALUES and CHOICES["mask"] == mc.MASK_MODES
    assert CHOICES["names.proposer"] == mc.PROPOSERS and CHOICES["voting"] == mc.VOTING_MODES
    assert CHOICES["linking"] == LINKING_MODES and CHOICES["canonical"] == CANONICAL_MODES
    assert CHOICES["names.variant"] == NAME_VARIANTS
    assert CHOICES["names.single_letter"] == SINGLE_LETTER_MODES
    assert CHOICES["channels"] == CHANNEL_SET_NAMES and CHOICES["code.matrix"] == MATRICES
    fields = set(mc.Config.__dataclass_fields__)
    assert set(NATIVE_NAMES.values()) | {"message_bits", "registry_seed"} == fields
    # i profili dell'adapter sono quelli della libreria (salvo il modulo, decisione O)
    a_mm = {NATIVE_NAMES[k]: v for k, v in MultimarkAdapter.PROFILE.items()}
    a_mc = {NATIVE_NAMES[k]: v for k, v in McssAdapter.PROFILE.items()}
    lib_mm, lib_mc = vars(mc.multimark_base()), vars(mc.mcss_profile())
    assert {k: lib_mc[k] for k in a_mc} == a_mc
    diff = {k for k in a_mm if lib_mm[k] != a_mm[k]}
    assert diff == {"names_module_level"}  # decisione O provvisoria: true nel benchmark


def test_native_hparams_and_validation(cfg: ExperimentConfig) -> None:
    a = _adapter(cfg, "multimark")
    native = a.to_native_hparams({"names": {"variant": "V1"}, "t": 4})
    assert native["names_variant"] == "V1" and native["t"] == 4
    assert native["message_bits"] == 24 and native["registry_seed"] == cfg.global_seed
    assert native == a.to_native_hparams({"names.variant": "V1", "t": 4})
    bad: list[dict[str, Any]] = [
        {"t": 3},
        {"names.variant": "V3"},
        {"names.include_params": 1},  # bool, non intero
        {"mask": True},
        {"color": "red"},
        {"names.proposer": "neural"},  # P3
        {"voting": "weighted"},  # senza channel_weights
        {"channel_weights": 3},
    ]
    for hp in bad:
        with pytest.raises(ConfigError):
            a.to_native_hparams(hp)
    a.to_native_hparams({"voting": "weighted", "channel_weights": "w.json"})


def test_config_hash_excludes_registry_seed(cfg: ExperimentConfig) -> None:
    a = _adapter(cfg, "multimark")
    native = a.to_native_hparams({})
    expected = config_hash({k: v for k, v in native.items() if k != "registry_seed"}, "c" * 40)
    assert a.config_hash({}) == expected
    assert a.config_hash({}) != config_hash(native, "c" * 40)
    assert a.config_hash({}) != _adapter(cfg, "mcss").config_hash({})


def test_messages(cfg: ExperimentConfig) -> None:
    mm, mc = _adapter(cfg, "multimark"), _adapter(cfg, "mcss")
    model = cfg.models_catalog["qwen25_coder_7b"]
    m = mm.message_for(model, "humaneval/0", "python", 3)
    assert m is not None and len(m) == 24 and set(m) <= {"0", "1"}
    assert m == message_bits(cfg.global_seed, "multimark", "humaneval/0", "python",
                             model.model_id, 3)  # fmt: skip
    assert m == mm.message_for(model, "humaneval/0", "python", 3)
    assert m != mc.message_for(model, "humaneval/0", "python", 3)  # etichetta per metodo
    assert mm.message_for(model, "humaneval/0", "python", None) is not None  # negativi


def test_bits_correct_comes_from_extra(cfg: ExperimentConfig) -> None:
    a = _adapter(cfg, "multimark")

    def res(status: str, extra: dict[str, Any], score: float | None = 3.0) -> WorkerResult:
        return WorkerResult(item_id="x", sample_index=None, status=status, raw_output=None,
                            code=None, score=score, native_decision=None,
                            decoded_message="0" * 24, extra=extra)  # fmt: skip

    assert a.bits_correct(res("OK", {"bits_correct": 21})) == 21  # non int(score)
    assert a.bits_correct(res("OK", {"bits_correct": None})) is None
    assert a.bits_correct(res("FAILED", {"bits_correct": 21})) is None


# ---------------------------------------------------------------- shim


def _request(cfg: ExperimentConfig, method: str, op: str) -> WorkerRequest:
    a = _adapter(cfg, method)
    model = cfg.models_catalog["qwen25_coder_7b"]
    return a.build_request(op, model, {}, "k1", {}, "", REPO_ROOT, "cpu")


def _shim(cfg: ExperimentConfig, method: str, op: str) -> Any:
    sys.path.insert(0, str(LIBRARY))
    sys.path.insert(0, str(REPO_ROOT / "shims"))
    try:
        if method == "mcss":
            from bench_shims.mcss.__main__ import McssShim as Shim
        else:
            from bench_shims.multimark.__main__ import MultimarkShim as Shim
        shim = Shim()
        shim.setup(_request(cfg, method, op))
        return shim
    finally:
        sys.path.remove(str(LIBRARY))
        sys.path.remove(str(REPO_ROOT / "shims"))


def _item(code: str, message: str | None) -> WorkerItem:
    return WorkerItem(item_id="s1", language="python", seed=0, prompt_messages=None, code=code,
                      context_prompt=None, expected_message=message, n=1)  # fmt: skip


@needs_library
@pytest.mark.parametrize("method", METHODS)
def test_shim_round_trip(cfg: ExperimentConfig, method: str) -> None:
    sys.path.insert(0, str(LIBRARY))
    try:
        from multimark.synthetic import generate
    finally:
        sys.path.remove(str(LIBRARY))
    code = generate(7, n_statements=40, n_functions=2)
    message = "011010011100101011110000"
    shim = _shim(cfg, method, "embed")
    (embedded,) = shim.embed(_item(code, message))
    assert embedded.status == "OK" and embedded.code != code and embedded.error is None
    assert embedded.extra["n_sites"] >= 32 and embedded.extra["reason"] is None
    detected = shim.detect(_item(embedded.code, message))
    assert detected.status == "OK" and detected.decoded_message == message
    assert detected.extra["bits_correct"] == 24 and detected.score is not None
    assert detected.score > 5
    for field in ("n_votes", "n_sites_by_channel", "score_nreg16", "best_nreg1024", "sites",
                  "decode_status", "n_erasures", "library_version"):  # fmt: skip
        assert field in detected.extra
    assert detected.native_decision is True
    # Sul codice non marcato il punteggio è basso.
    plain = shim.detect(_item(code, message))
    assert plain.status == "OK" and plain.score < detected.score


@needs_library
def test_shim_failures(cfg: ExperimentConfig) -> None:
    shim = _shim(cfg, "multimark", "embed")
    (none,) = shim.embed(_item("def f(*args):\n    return 3\n", "0" * 24))
    assert none.status == "FAILED" and none.error == "no_sites"
    (broken,) = shim.embed(_item("def (:\n", "0" * 24))
    assert broken.status == "FAILED" and broken.error == "syntax_error"
    det = shim.detect(_item("def f(*args):\n    return 3\n", "0" * 24))
    assert det.status == "FAILED" and det.error == "no_sites" and det.score is None
    with pytest.raises(ValueError, match="expected_message"):
        shim.detect(_item("x = 1\n", None))
