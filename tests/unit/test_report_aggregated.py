"""``scripts/report_aggregated.py`` (rilevazione aggregata §5.10, P6) su tabelle sintetiche:
punteggio identico alla libreria a k = 1, gruppi da problemi distinti e deterministici, esclusione
dei positivi non inseriti, FPR sotto H0 nella verifica a metà, soglie mai sovrascritte e lette
solo se congelate sul test."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

agg = _load("report_aggregated")
MODEL = "qwen25_coder_7b"
CFG = agg.Config("multimark", "e52b040770df", "prudent")


class FakeStore:
    def __init__(self, root: Path, data: dict[str, pd.DataFrame]) -> None:
        self.root, self.data = root, data

    def exists(self, ref: Any) -> bool:
        return str(ref.path) in self.data

    def read_table(self, ref: Any) -> pd.DataFrame:
        return self.data[str(ref.path)]


def _table(a: list[float], n: list[float], problems: list[str], *, status: str = "OK",
           embed: str = "OK", prefix: str = "s") -> pd.DataFrame:  # fmt: skip
    rows = []
    for i, (ai, ni, p) in enumerate(zip(a, n, problems, strict=True)):
        rows.append({
            "sample_id": f"{prefix}{i}", "problem_key": p, "status": status, "embed_status": embed,
            "score": agg.binom_score(ni, ai) if status == "OK" else math.nan,
            "extra_json": json.dumps({"agreement": ai, "n_votes": ni}),
        })  # fmt: skip
    return pd.DataFrame(rows)


def test_binom_score_is_the_library_formula() -> None:
    assert agg.binom_score(10, 10) == pytest.approx(10 * math.log10(2))
    assert agg.binom_score(10, 0) == 0.0
    assert agg.binom_score(0, 0) == agg.MIN_SCORE
    tail = sum(math.comb(24, j) for j in range(18, 25)) / 2**24
    assert agg.binom_score(24, 18) == pytest.approx(-math.log10(tail), abs=1e-12)
    assert agg.binom_score(4.5, 4.0) > 0  # pesi non interi: approssimazione normale


def test_identity_at_k1() -> None:
    t = _table([3, 5, 7], [4, 6, 9], ["a", "b", "c"])
    assert agg.identity(t) == {"n": 3, "mismatch": 0, "max_diff": 0.0}
    t.loc[1, "score"] = t.loc[1, "score"] + 0.1
    r = agg.identity(t)
    assert r["mismatch"] == 1 and r["max_diff"] == pytest.approx(0.1)


def test_votes_excludes_failed_embedding_and_zeroes_failed_detection() -> None:
    ok = _table([3], [4], ["a"])
    failed_embed = _table([3], [4], ["b"], embed="FAILED", prefix="f")
    failed_det = _table([3], [4], ["c"], status="FAILED", prefix="d")
    pos = agg.votes(pd.concat([ok, failed_embed, failed_det], ignore_index=True), positives=True)
    assert list(pos["problem_key"]) == ["a", "c"] and list(pos["N"]) == [4.0, 0.0]
    neg = agg.votes(pd.concat([ok, failed_embed], ignore_index=True), positives=False)
    assert len(neg) == 2  # i negativi non hanno inserimento: nessuna esclusione


def test_groups_are_from_distinct_problems_and_deterministic() -> None:
    problems = [f"p{i}" for i in range(5) for _ in range(3)]
    pool = pd.DataFrame({"problem_key": problems, "A": [1.0] * 15, "N": [1.0] * 15})
    # marcatore: N = indice del problema + 1, così la somma dice quali problemi ci sono
    pool["N"] = [float(int(p[1:]) + 1) for p in problems]
    sums = agg.groups(pool, 5, 50, seed=7)
    assert sums is not None and set(sums[:, 1]) == {15.0}  # tutti e 5 i problemi, una volta sola
    assert agg.groups(pool, 6, 10, seed=7) is None
    a = agg.groups(pool, 2, 100, seed=11)
    b = agg.groups(pool.sample(frac=1.0, random_state=3), 2, 100, seed=11)
    assert np.array_equal(a, b)  # indipendente dall'ordine delle righe
    assert not np.array_equal(a, agg.groups(pool, 2, 100, seed=12))


def test_decreases_beyond_two_sigma() -> None:
    assert not agg.decreases(0.50, 0.49) and agg.decreases(0.50, 0.40)
    assert not agg.decreases(0.0, 0.0) and not agg.decreases(0.3, 0.9)


def test_half_limit() -> None:
    assert agg.half_limit(0.01, 1000, 1000) == pytest.approx(0.01 + 2 * math.sqrt(0.0099 * 0.002))
    assert agg.half_limit(0.01, 100, 100) > agg.half_limit(0.01, 1000, 1000)


def test_rate_with_wilson_and_bootstrap() -> None:
    hits = np.array([1.0] * 30 + [0.0] * 70)
    r = agg.rate(hits, boot_seed=1)
    assert r["value"] == 0.3 and r["n"] == 100
    assert r["wilson_low"] < 0.3 < r["wilson_high"] and r["boot_low"] < 0.3 < r["boot_high"]
    assert agg.rate(hits, 1) == r  # deterministico
    assert math.isnan(agg.rate(np.array([]), 1)["value"])


# --------------------------------------------------------------------------- dati finti
def _store(root: Path, seed: int = 0) -> FakeStore:
    """Negativi sotto H0 (A ~ Bin(N, 1/2)) e positivi con concordanza alta, L1 e L2 dev."""
    from bench.domain.enums import Language
    from bench.pipeline.stages.detect import detection_ref
    from bench.pipeline.stages.prepare_data import native_ref

    rng = np.random.default_rng(seed)

    def neg(n_problems: int, per: int, prefix: str) -> pd.DataFrame:
        problems = [f"{prefix}{i}" for i in range(n_problems) for _ in range(per)]
        n = rng.integers(8, 40, size=len(problems)).astype(float)
        a = rng.binomial(n.astype(int), 0.5).astype(float)
        return _table(list(a), list(n), problems, prefix=prefix)

    def pos(n_problems: int, prefix: str) -> pd.DataFrame:
        problems = [f"{prefix}{i}" for i in range(n_problems) for _ in range(3)]
        n = rng.integers(1, 6, size=len(problems)).astype(float)
        a = rng.binomial(n.astype(int), 0.9).astype(float)
        return _table(list(a), list(n), problems, prefix=prefix)

    def det(subject: str, level: str) -> str:
        return str(detection_ref(CFG.method, MODEL, CFG.config_hash, subject, level, "python",
                                 "dev").path)  # fmt: skip

    l1 = neg(300, 4, "n1_")
    data = {
        det("neg_human", "L1"): l1,
        det("neg_human", "L2"): neg(300, 4, "n2_"),
        det("neg_integration", "L2"): neg(200, 2, "ni_"),
        str(native_ref(Language("python")).path): l1[["sample_id"]].iloc[: len(l1) - 40],
        det("positives", "L1"): pos(60, "q1_"),
        det("positives", "L2"): pos(40, "q2_"),
    }
    return FakeStore(root, data)


def _run(store: FakeStore, args: list[str]) -> int:
    return int(agg.main([*args, "--config", f"{CFG.method}:{CFG.config_hash}:{CFG.label}",
                         "--models", MODEL, "paths=local"] if args[0] == "verify" else
                        [*args, "--models", MODEL, "paths=local"], store=store))  # fmt: skip


def test_dev_negatives_use_the_calibration_parts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    neg = agg.dev_negatives(store, CFG, MODEL)
    assert neg is not None and len(neg) == (1200 - 40) + 1200 + 400  # nativi di L1 soltanto


def test_verify_passes_under_h0(local_root: Path, tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = tmp_path / "verify.txt"
    assert _run(store, ["verify", "--report", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "ALL CHECKS PASSED" in text and "k = 1 identity on dev negatives" in text
    assert "determinism (two runs, same thresholds): OK" in text
    assert "## split halves: FPR above the limit in" in text
    for part in ("L1", "L2", "integration_L1L2"):  # FPR di ogni metà per livello, sempre
        assert text.count(f"{part:>18}") == 2 * len(agg.KS)
    assert _run(store, ["verify", "--report", str(report)]) == 1  # mai sovrascritto


def test_verify_fails_on_a_score_mismatch(local_root: Path, tmp_path: Path) -> None:
    store = _store(tmp_path)
    key = next(k for k in store.data if "positives/L1" in k)
    store.data[key].loc[0, "score"] = 99.0
    report = tmp_path / "verify.txt"
    assert _run(store, ["verify", "--report", str(report)]) == 1
    assert "SOME CHECKS FAILED" in report.read_text(encoding="utf-8")


def test_calibrate_then_evaluate(local_root: Path, tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from bench.hpo.posthoc_freeze import aggregated_path

    store = _store(tmp_path)
    monkeypatch.setattr(agg, "registered_configs", lambda: [CFG])
    assert _run(store, ["calibrate", "--report", str(tmp_path / "cal.txt")]) == 0
    path = tmp_path / aggregated_path(CFG.method, MODEL, CFG.config_hash)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(int(k) for k in data["k"]) == list(agg.KS)
    assert all(v["achieved_fpr"] <= 0.01 for v in data["k"].values())
    cal_text = (tmp_path / "cal.txt").read_text(encoding="utf-8")
    assert "split halves" in cal_text and cal_text.count(f"{'L2':>18}") == 2 * len(agg.KS)
    before = path.read_bytes()
    assert _run(store, ["calibrate", "--report", str(tmp_path / "cal2.txt")]) == 1
    assert path.read_bytes() == before  # soglie mai sovrascritte
    out = tmp_path / "tables"
    assert _run(store, ["evaluate", "--split", "dev", "--out", str(out),
                        "--report", str(tmp_path / "ev.txt")]) == 0  # fmt: skip
    frame = pd.read_csv(out / "aggregated_L1_dev.csv")
    clean = frame[frame["condition"] == "no attack"].sort_values("k")
    assert list(clean["k"]) == list(agg.KS)
    assert clean["tpr"].iloc[-1] >= clean["tpr"].iloc[0]  # più campioni, più rilevazioni
    assert {"tpr_boot_low", "tpr_wilson_low", "fpr"} <= set(frame.columns)
    with pytest.raises(SystemExit, match="not frozen"):
        _run(store, ["evaluate", "--split", "test", "--out", str(out),
                     "--report", str(tmp_path / "ev2.txt")])  # fmt: skip


# --------------------------------------------------------------------------- v2 della verifica
ATTACKED = ("T2.5 n=2,position=around", "attacked/T2.5/abc123")


def _with_attack(store: FakeStore) -> FakeStore:
    """Aggiunge la cella attaccata (positivi senza segnale, A ~ Bin(N, 1/2)) su L1 e L2 dev."""
    from bench.pipeline.stages.detect import detection_ref

    rng = np.random.default_rng(7)
    for level in ("L1", "L2"):
        problems = [f"t{level}_{i}" for i in range(50) for _ in range(3)]
        n = rng.integers(1, 6, size=len(problems)).astype(float)
        a = rng.binomial(n.astype(int), 0.5).astype(float)
        ref = detection_ref(CFG.method, MODEL, CFG.config_hash, ATTACKED[1], level, "python", "dev")
        store.data[str(ref.path)] = _table(list(a), list(n), problems, prefix=f"t{level}")
    return store


def test_problem_wilson_and_worst_drop() -> None:
    lo, hi = agg.problem_wilson(0.05, 50)
    glo, ghi = agg.problem_wilson(0.05, 1000)
    assert lo < glo and hi > ghi  # con n = problemi l'intervallo è più largo
    drop, s_groups, s_problems = agg.worst_drop([0.08, 0.12, 0.09, math.nan], 1000, 50)
    assert math.isclose(drop, 0.03) and s_problems > s_groups > 0
    assert agg.worst_drop([0.1, 0.2, 0.3], 1000, 50)[0] == 0.0
    assert agg.mean_score(np.array([1.0, 3.0])) == "2.000"
    assert "1 no votes" in agg.mean_score(np.array([1.0, -math.inf]))


def test_decreasing_tpr_blocks_only_without_attack(
    local_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agg, "attack_subjects", lambda: [ATTACKED])
    monkeypatch.setattr(agg, "decreases", lambda before, after, n=agg.N_GROUPS: True)
    store = _with_attack(_store(tmp_path))
    report = tmp_path / "v2.txt"
    assert _run(store, ["verify", "--report", str(report)]) == 1  # senza attacco: bloccante
    text = report.read_text(encoding="utf-8")
    assert "L1 no attack" in text and "FAIL (decreasing)" in text
    assert "WARN (decreasing, attacked: not blocking)" in text
    assert "TPR decreasing in k on attacked rows: 2" in text
    assert "problems 50; mean score_k" in text and "2σ problems" in text
    for key in [k for k in store.data if "/positives/" in k]:
        del store.data[key]  # restano solo le righe attaccate: avviso, la verifica passa
    report2 = tmp_path / "v2b.txt"
    assert _run(store, ["verify", "--report", str(report2)]) == 0
    text2 = report2.read_text(encoding="utf-8")
    assert "ALL CHECKS PASSED" in text2 and "FAIL (decreasing)" not in text2
    assert "Wilson n=problems" in text2


def test_evaluate_reports_fpr_by_level(local_root: Path, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setattr(agg, "registered_configs", lambda: [CFG])
    store = _store(tmp_path)
    assert _run(store, ["calibrate", "--report", str(tmp_path / "cal.txt")]) == 0
    out = tmp_path / "tables"
    assert _run(store, ["evaluate", "--split", "dev", "--out", str(out),
                        "--report", str(tmp_path / "ev.txt")]) == 0  # fmt: skip
    text = (tmp_path / "ev.txt").read_text(encoding="utf-8")
    assert "# FPR of the aggregated detection by level" in text
    for level in ("L1", "L2"):
        frame = pd.read_csv(out / f"aggregated_{level}_dev.csv")
        assert set(frame["fpr_negatives"]) == {f"neg_human {level} dev"}
        assert (frame["n_neg_problems"] == 300).all()
        wide = frame["fpr_wilson_high_problems"] - frame["fpr_wilson_low_problems"]
        assert (wide >= frame["fpr_wilson_high"] - frame["fpr_wilson_low"] - 1e-12).all()
