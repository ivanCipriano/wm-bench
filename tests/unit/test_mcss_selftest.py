"""Selezione CodeNet della verifica di ``mcss`` (PIPELINE §4.4 punto 2; decisione del 13 novembre).

Pool (``export_inventory_inputs.pool_rows``), passaggio di ``role`` in ``inventory_sites.py`` e
selezione della fascia 32-39 bit (``select_mcss_selftest.py``).
"""

from __future__ import annotations

import csv
import json
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


# ---------------------------------------------------------------- selezione


def _row(sid: str, bits: int, *, vars_: int = 10, structural: int = 12, ok: bool = True) -> dict:  # type: ignore[type-arg]
    return {
        "sample_id": sid,
        "parse_ok": "True" if ok else "False",
        "n_bits_v1": str(bits),
        "n_vars": str(vars_),
        "n_sites_structural": str(structural),
        "n_sites_total": str(vars_ + structural),
    }


def test_band_is_closed_32_39_in_bits_and_needs_parsing() -> None:
    assert sel.BAND == (32, 39) and sel.MAX_FILES == 250
    assert [sel.in_band(_row("x", b)) for b in (31, 32, 39, 40)] == [False, True, True, False]
    assert not sel.in_band(_row("x", 35, ok=False))


def test_select_takes_all_when_fewer_and_the_seed_order_when_more() -> None:
    mcss = {f"s{i:03}": _row(f"s{i:03}", 32 + i % 10) for i in range(40)}
    band, chosen = sel.select(mcss, SEED)
    assert band == chosen == sorted(s for s, r in mcss.items() if sel.in_band(r))  # 32 file

    band, chosen = sel.select(mcss, SEED, limit=5)
    expected = sorted(band, key=lambda s: (derive_seed(SEED, "mcss-selftest", s), s))[:5]
    assert chosen == sorted(expected) and len(chosen) == 5
    # Indipendente dall'ordine delle righe dell'ingresso.
    assert sel.select(dict(reversed(list(mcss.items()))), SEED, limit=5)[1] == chosen


def _pool_files(tmp_path: Path, n: int) -> list[str]:
    pool = tmp_path / "L2_codenet_pool.jsonl"
    sids = [f"codenet/p{i:05}/python/s{i}" for i in range(n)]
    recs = [
        {
            "sample_id": s,
            "problem_key": s.rsplit("/", 2)[0],
            "role": "unselected" if i % 2 else "negative_dev",
            "split": None,
            "loc": 30,
            "code": f"x = {i}\n",
        }
        for i, s in enumerate(sids)
    ]
    pool.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    (tmp_path / "L2_codenet_pool.jsonl.manifest.json").write_text(
        json.dumps({"pool": {"global_seed": SEED}}), encoding="utf-8"
    )
    return sids


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_main_writes_selection_and_report_and_refuses_changes(tmp_path: Path) -> None:
    sids = _pool_files(tmp_path, 6)
    bits = [30, 32, 35, 39, 40, 33]  # in fascia: indici 1, 2, 3, 5
    _csv(
        tmp_path / "mcss.csv",
        [_row(s, b, vars_=12, structural=10) for s, b in zip(sids, bits, strict=True)],
    )
    _csv(
        tmp_path / "base.csv",
        [_row(s, 0, vars_=4, structural=9 + 20 * (i == 2)) for i, s in enumerate(sids)],
    )
    out, rep = tmp_path / "sel.jsonl", tmp_path / "rep.txt"
    argv = [
        "--pool", str(tmp_path / "L2_codenet_pool.jsonl"),
        "--mcss-table", str(tmp_path / "mcss.csv"), "--base-table", str(tmp_path / "base.csv"),
        "--out", str(out), "--report", str(rep),
    ]  # fmt: skip
    assert sel.main(argv) == 0
    chosen = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [c["sample_id"] for c in chosen] == [sids[i] for i in (1, 2, 3, 5)]
    assert chosen[0]["code"] == "x = 1\n" and chosen[0]["mcss_n_bits_v1"] == 32
    text = rep.read_text(encoding="utf-8")
    assert "files in band: 4" in text
    assert "selected: 4 (fewer than 250: all files in band)" in text
    assert "mean variables:        12.00 vs 11.9 (+0.8%, within ±30%)" in text
    assert "structural sites: 10.00 vs 10.9 (-8.3%, within ±30%)" in text
    assert "files with n_sites_total >= 32: 1" in text  # solo l'indice 2 (4 + 29 siti)
    assert "selected by role: negative_dev=1, unselected=3" in text

    assert sel.main(argv) == 0  # stesso contenuto: nessuna riscrittura
    _csv(tmp_path / "mcss.csv", [_row(s, 36) for s in sids])
    assert sel.main(argv) == 1  # selezione diversa: il file esistente non si tocca
    assert len(out.read_text(encoding="utf-8").splitlines()) == 4


def test_main_rejects_tables_of_another_pool(tmp_path: Path) -> None:
    sids = _pool_files(tmp_path, 3)
    _csv(tmp_path / "mcss.csv", [_row(s, 33) for s in sids[:2]])
    _csv(tmp_path / "base.csv", [_row(s, 0) for s in sids])
    argv = [
        "--pool", str(tmp_path / "L2_codenet_pool.jsonl"),
        "--mcss-table", str(tmp_path / "mcss.csv"), "--base-table", str(tmp_path / "base.csv"),
        "--out", str(tmp_path / "sel.jsonl"),
    ]  # fmt: skip
    assert sel.main(argv) == 1
    assert not (tmp_path / "sel.jsonl").exists()


def test_missing_seed_in_manifest_is_an_error(tmp_path: Path) -> None:
    pool = tmp_path / "p.jsonl"
    pool.write_text("", encoding="utf-8")
    with pytest.raises(sel.SelectionError, match="global_seed"):
        sel.pool_seed(pool)
