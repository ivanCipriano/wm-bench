"""``scripts/report_capacity.py``: le tabelle non si sovrascrivono (secondo giro di P2: stesso nome
del file del primo giro, configurazione diversa)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_inventory_scripts import _load

cap = _load("report_capacity")


def test_tables_are_written_once(tmp_path: Path) -> None:
    path = tmp_path / "capacity" / "capacity_by_sites_x.csv"
    rows = [{"band": "1-7", "n": 3}]
    cap.write_table(path, rows)
    first = path.read_bytes()
    cap.write_table(path, rows)  # stesso contenuto: nessun errore, file uguale
    assert path.read_bytes() == first
    with pytest.raises(cap.CapacityError, match="another --out-dir"):
        cap.write_table(path, [{"band": "1-7", "n": 4}])
    assert path.read_bytes() == first
