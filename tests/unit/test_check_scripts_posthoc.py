"""``scripts/check_attacks.py`` e ``scripts/check_m12.py`` per i metodi post-hoc (P6): punti scelti
per livello e metodi multi-bit; per i cinque metodi nulla cambia."""

from __future__ import annotations

from tests.unit.test_inventory_scripts import _load

attacks = _load("check_attacks")
m12 = _load("check_m12")


def test_chosen_points_by_level() -> None:
    from bench.chains import POSTHOC_POINTS

    t14 = POSTHOC_POINTS["L2"]["T1.4"]
    for method in ("sweet", "acw", "stone", "promptmark", "mcgmark"):  # i cinque: tutti i punti
        assert attacks.chosen_point(method, "L1", "T1.4", "anything")
    assert attacks.chosen_point("mcss", "L1", "T1.4", "anything")  # L1: suite completa
    assert attacks.chosen_point("multimark", "L2", "T1.4", t14[0])
    assert not attacks.chosen_point("multimark", "L2", "T1.4", "not-a-chosen-point")
    assert attacks.chosen_point("multimark", "L2", "T2.5", "any-point")  # tutti i punti
    assert attacks.chosen_point("mcss", "L3", "T1.4", POSTHOC_POINTS["L3"]["T1.4"][0])
    assert not attacks.chosen_point("mcss", "L3", "T1.4", t14[1])
    assert attacks.chosen_point("mcss", "L3", "T2.4k", "any-point")


def test_m12_multibit_methods() -> None:
    assert set(m12.MULTIBIT) == {"mcgmark", "mcss", "multimark"}
    assert m12.POSTHOC == ("mcss", "multimark") == attacks.POSTHOC
