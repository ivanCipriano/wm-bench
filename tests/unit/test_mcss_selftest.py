"""Selezione CodeNet della verifica di ``mcss`` (PIPELINE §4.4 punto 2; decisione del 13 novembre).

Pool (``export_inventory_inputs.pool_rows``), passaggio di ``role`` in ``inventory_sites.py`` e
selezione della fascia 32-39 bit (``select_mcss_selftest.py``).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from bench_contracts import derive_seed

from tests.unit.test_inventory_scripts import LIBRARY, _load, needs_library

export = _load("export_inventory_inputs")
inv = _load("inventory_sites")
sel = _load("select_mcss_selftest")

SEED = 20261001
SELECTION = {
    "problems": [
        {
            "problem_key": "codenet/p00001",
            "submissions": {
                "python": {
                    "canonical": {"submission_id": "s1", "ext": "py"},
                    "negatives": [{"submission_id": "s2", "ext": "py"}],
                }
            },
        },
        {
            "problem_key": "codenet/p00002",
            "submissions": {
                "python": {
                    "canonical": {"submission_id": "s5", "ext": "py"},
                    "negatives": [{"submission_id": "s6", "ext": "py"}],
                }
            },
        },
    ]
}
SPLITS: dict[str, str | None] = {"codenet/p00001": "test", "codenet/p00002": "dev"}


# ---------------------------------------------------------------- pool


def test_submission_roles_follow_the_problem_split() -> None:
    roles = export.submission_roles(SELECTION, SPLITS)
    assert roles == {
        ("p00001", "s1"): "canonical_test",
        ("p00001", "s2"): "negative_test",
        ("p00002", "s5"): "canonical_dev",
        ("p00002", "s6"): "negative_dev",
    }


def _write(root: Path, pid: str, sub: str, code: str) -> Path:
    path = root / pid / "Python" / f"{sub}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("utf-8"))
    return path


def test_pool_excludes_test_negatives_and_canonicals(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "p00001", "s1", "print(1)\r\n"),  # canonica di test: esclusa per ruolo
        _write(tmp_path, "p00001", "s2", "print(2)\n"),  # negativo di test: escluso per ruolo
        _write(tmp_path, "p00001", "s3", "print(3)\n"),  # stesso codice di un negativo: escluso
        _write(tmp_path, "p00009", "s4", "print(1)\n"),  # stesso codice della canonica: escluso
        _write(tmp_path, "p00002", "s5", "print(5)\r\n"),  # canonica di sviluppo: resta
        _write(tmp_path, "p00002", "s6", "print(6)\n"),  # negativo di sviluppo: resta
        _write(tmp_path, "p00009", "s9", "x = 1\ny = 2\n"),  # problema non scelto: resta
    ]
    roles = export.submission_roles(SELECTION, SPLITS)
    rows, stats = export.pool_rows(files, roles, SPLITS, {"print(2)\n", "print(3)\n"})
    assert [r["sample_id"] for r in rows] == [
        "codenet/p00002/python/s5",
        "codenet/p00002/python/s6",
        "codenet/p00009/python/s9",
    ]
    assert [r["role"] for r in rows] == ["canonical_dev", "negative_dev", "unselected"]
    assert [r["split"] for r in rows] == ["dev", "dev", None]
    assert rows[0]["code"] == "print(5)\n"  # fine riga normalizzati come nel loader
    assert rows[2]["loc"] == 2 and rows[2]["source"] == "codenet_pool"
    assert stats == {
        "excluded_code": 2,
        "excluded_role": 2,
        "files": 7,
        "kept": 3,
        "role=canonical_dev": 1,
        "role=negative_dev": 1,
        "role=unselected": 1,
    }
    # Il campo facoltativo ``role`` va nel JSONL solo per le righe che lo hanno.
    lines = export.jsonl_bytes(rows).decode("utf-8").splitlines()
    assert list(json.loads(lines[0])) == [*export.ROW_FIELDS, "role"]


def test_pool_group_is_not_in_the_default_export() -> None:
    assert "L2_codenet_pool" not in {g.name for g in export.groups_for(["m"])}


@needs_library
def test_inventory_passes_the_role_through(tmp_path: Path) -> None:
    rows, _ = export.pool_rows(
        [
            _write(tmp_path / "d", "p00002", "s6", "def f(items):\n    out = []\n    return out\n"),
            _write(tmp_path / "d", "p00009", "s9", "total = 0\ntotal = total + 1\n"),
        ],
        export.submission_roles(SELECTION, SPLITS),
        SPLITS,
        set(),
    )
    pool = tmp_path / "L2_codenet_pool.jsonl"
    pool.write_bytes(export.jsonl_bytes(rows))
    summary = tmp_path / "s.txt"
    argv = ["--input", str(pool), "--out-dir", str(tmp_path / "t"), "--library", str(LIBRARY)]
    argv += ["--channels", "extended"]
    assert inv.main([*argv, "--summary", str(summary)]) == 0
    table = tmp_path / "t" / "L2_codenet_pool.canonical-kind_guards-on_params-off.csv"
    with table.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header, got = reader.fieldnames, list(reader)
    assert header is not None and header[: len(inv.INPUT_FIELDS) + 1] == [
        *inv.INPUT_FIELDS,
        "role",
    ]
    assert [r["role"] for r in got] == ["negative_dev", "unselected"]
    text = summary.read_text(encoding="utf-8")
    assert "Inventory L2_codenet_pool [role=unselected]" in text


# ---------------------------------------------------------------- cluster di test


def test_test_clusters_lists_non_test_problems_sharing_a_cluster() -> None:
    clusters = {"p00001": 0, "p00007": 0, "p00002": 1, "p00008": 1, "p00009": 2}
    splits: dict[str, str | None] = {
        "codenet/p00001": "test",
        "codenet/p00002": "dev",
        "codenet/p00003": "test",  # senza cluster
    }
    got = export.test_clusters(clusters, splits)
    assert got == {"test_problems": ["p00001", "p00003"], "same_cluster_as_test": ["p00007"]}


# ---------------------------------------------------------------- selezione


def _row(sid: str, bits: int, *, vars_: int = 10, structural: int = 12, ok: bool = True) -> dict:  # type: ignore[type-arg]
    return {
        "sample_id": sid,
        "parse_ok": "True" if ok else "False",
        "n_bits_v1": str(bits),
        "n_vars": str(vars_),
        "n_vars_module": str(vars_ // 2),
        "n_sites_structural": str(structural),
        "n_sites_total": str(vars_ + structural),
    }


def _pool(n: int, *, problems: int | None = None) -> dict[str, Any]:
    """``n`` sottomissioni distribuite su ``problems`` problemi (default: uno a testa)."""
    problems = problems or n
    return {
        f"codenet/p{i % problems:05}/python/s{i:03}": {
            "problem_key": f"codenet/p{i % problems:05}",
            "role": "unselected",
            "split": None,
        }
        for i in range(n)
    }


def test_constants_follow_pipeline_v12() -> None:
    assert sel.BAND == (32, 39) and sel.MAX_FILES == 250 and sel.MIN_FILES == 100
    assert (sel.PAPER_BITS, sel.BITS_TOLERANCE) == (34.6, 0.10)


def test_eligible_drops_test_split_test_clusters_and_parse_errors() -> None:
    pool = {
        "codenet/p00001/python/a": {"problem_key": "codenet/p00001", "split": "test"},
        "codenet/p00002/python/b": {"problem_key": "codenet/p00002", "split": None},
        "codenet/p00003/python/c": {"problem_key": "codenet/p00003", "split": "dev"},
        "codenet/p00004/python/d": {"problem_key": "codenet/p00004", "split": None},
    }
    mcss = {s: _row(s, 35) for s in pool}
    mcss["codenet/p00004/python/d"] = _row("codenet/p00004/python/d", 35, ok=False)
    ids, excluded = sel.eligible(pool, mcss, {"p00002"})
    assert ids == ["codenet/p00003/python/c"]
    assert excluded == {"split_test": 1, "test_cluster": 1, "parse_error": 1}


def test_band_widens_to_32_plus_below_the_minimum() -> None:
    mcss = {f"s{i:03}": _row(f"s{i:03}", 30 + i % 15) for i in range(150)}
    band, cands, widened = sel.candidates(mcss, sorted(mcss))
    assert len(band) == 80 and widened  # 32..39: 8 valori su 15
    assert len(cands) == 130 and all(int(mcss[s]["n_bits_v1"]) >= 32 for s in cands)
    more = {f"s{i:03}": _row(f"s{i:03}", 32 + i % 8) for i in range(120)}
    band, cands, widened = sel.candidates(more, sorted(more))
    assert band == cands and len(band) == 120 and not widened


def test_select_one_per_problem_when_enough_problems() -> None:
    pool = _pool(300, problems=150)  # due sottomissioni per problema
    chosen, one = sel.select(pool, sorted(pool), SEED)
    assert one and len(chosen) == 150
    assert len({pool[s]["problem_key"] for s in chosen}) == 150
    # la sottomissione tenuta è la prima del problema nell'ordine del seme
    for sid in chosen:
        key = pool[sid]["problem_key"]
        mates = [s for s in pool if pool[s]["problem_key"] == key]
        assert sid == min(mates, key=lambda s: (derive_seed(SEED, "mcss-selftest", s), s))
    big = _pool(400)
    capped, _ = sel.select(big, sorted(big), SEED)
    assert len(capped) == 250


def test_select_round_robin_when_few_problems() -> None:
    pool = _pool(120, problems=40)  # 40 problemi < 100: tre a testa, tutti presi
    chosen, one = sel.select(pool, sorted(pool), SEED)
    assert not one and chosen == sorted(pool)
    chosen, _ = sel.select(pool, sorted(pool), SEED, limit=50)
    per_problem = Counter(pool[s]["problem_key"] for s in chosen)
    assert len(per_problem) == 40 and max(per_problem.values()) == 2  # prima uno a testa
    assert sel.select(pool, list(reversed(sorted(pool))), SEED, limit=50)[0] == chosen


def _write_pool(tmp_path: Path, pool: dict[str, Any], same_cluster: list[str]) -> Path:
    path = tmp_path / "L2_codenet_pool.jsonl"
    recs = [{"sample_id": s, **r, "loc": 30, "code": f"# {s}\n"} for s, r in pool.items()]
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    (tmp_path / "L2_codenet_pool.jsonl.manifest.json").write_text(
        json.dumps({"pool": {"global_seed": SEED}}), encoding="utf-8"
    )
    (tmp_path / "L2_codenet_pool.test_clusters.json").write_text(
        json.dumps({"test_problems": [], "same_cluster_as_test": same_cluster}), encoding="utf-8"
    )
    return path


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _argv(tmp_path: Path, pool: Path) -> list[str]:
    return [
        "--pool", str(pool),
        "--mcss-table", str(tmp_path / "mcss.csv"), "--base-table", str(tmp_path / "base.csv"),
        "--out", str(tmp_path / "sel.jsonl"), "--report", str(tmp_path / "rep.txt"),
    ]  # fmt: skip


def test_main_writes_selection_and_report_and_refuses_changes(tmp_path: Path) -> None:
    pool = _pool(130)
    sids = sorted(pool)
    pool[sids[0]]["split"] = "test"  # escluso: problema di test
    pool[sids[1]]["role"] = "negative_dev"
    path = _write_pool(tmp_path, pool, same_cluster=["p00002"])  # sids[2] escluso: cluster
    mcss_rows = [_row(s, 33 + i % 3, vars_=12, structural=10) for i, s in enumerate(sids)]
    _csv(tmp_path / "mcss.csv", mcss_rows)
    base_rows = [_row(s, 0, vars_=3, structural=9 + 20 * (i < 10)) for i, s in enumerate(sids)]
    _csv(tmp_path / "base.csv", base_rows)
    argv = _argv(tmp_path, path)
    assert sel.main(argv) == 0
    out = tmp_path / "sel.jsonl"
    chosen = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(chosen) == 128 and sids[0] not in {c["sample_id"] for c in chosen}
    assert chosen[0]["mcss_n_vars_module"] == 6
    text = (tmp_path / "rep.txt").read_text(encoding="utf-8")
    assert "excluded split == test: 1\n" in text
    assert "excluded same identical_problem_clusters cluster as a test problem: 1\n" in text
    assert "band used: 32 <= n_bits_v1 <= 39\n" in text
    assert "one submission per problem: yes\n" in text
    assert "selected: 128\n" in text and "WARNING" not in text
    assert "mean variables:             12.00 vs 11.9 (+0.8%)" in text
    assert "mean n_bits_v1 (criterion): " in text and ": PASS (±10%)" in text
    assert "files with n_sites_total >= 13: 8" in text  # sids[1], sids[3..9]
    assert "selected by role: negative_dev=1, unselected=127" in text

    assert sel.main(argv) == 0  # stesso contenuto: nessuna riscrittura
    _csv(tmp_path / "mcss.csv", [_row(s, 36) for s in sids])
    assert sel.main(argv) == 1  # selezione diversa: il file esistente non si tocca
    assert len(out.read_text(encoding="utf-8").splitlines()) == 128


def test_main_widens_and_warns_below_the_minimum(tmp_path: Path) -> None:
    pool = _pool(30)
    sids = sorted(pool)
    path = _write_pool(tmp_path, pool, same_cluster=[])
    _csv(tmp_path / "mcss.csv", [_row(s, 30 + i) for i, s in enumerate(sids)])
    _csv(tmp_path / "base.csv", [_row(s, 0) for s in sids])
    assert sel.main(_argv(tmp_path, path)) == 0
    text = (tmp_path / "rep.txt").read_text(encoding="utf-8")
    assert "eligible files in 32-39: 8\n" in text
    assert "band used: n_bits_v1 >= 32 (widened" in text
    assert "selected: 28\n" in text and "WARNING: fewer than 100 files" in text
    assert "one submission per problem: no\n" in text


def test_main_rejects_tables_of_another_pool(tmp_path: Path) -> None:
    pool = _pool(3)
    sids = sorted(pool)
    path = _write_pool(tmp_path, pool, same_cluster=[])
    _csv(tmp_path / "mcss.csv", [_row(s, 33) for s in sids[:2]])
    _csv(tmp_path / "base.csv", [_row(s, 0) for s in sids])
    assert sel.main(_argv(tmp_path, path)) == 1
    assert not (tmp_path / "sel.jsonl").exists()


def test_missing_seed_or_clusters_is_an_error(tmp_path: Path) -> None:
    pool = tmp_path / "p.jsonl"
    pool.write_text("", encoding="utf-8")
    with pytest.raises(sel.SelectionError, match="global_seed"):
        sel.pool_seed(pool)
    with pytest.raises(sel.SelectionError, match="same_cluster_as_test"):
        sel.read_test_clusters(sel.default_test_clusters(pool))
