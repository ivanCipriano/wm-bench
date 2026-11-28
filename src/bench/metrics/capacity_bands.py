"""Capacità per fascia di siti e di lunghezza del problema (PIPELINE di multimark §5.6).

Funzioni comuni a ``scripts/report_capacity.py`` (controlli su dev, da riga di comando) e al
report (``bench.report``, opzione ``capacity``: curva capacità–lunghezza di MCGMark sul test).
Prima del 28 novembre 2026 stavano nello script; il comportamento è lo stesso.

Per una cella (senza attacco o con attacco già rilevato) :func:`cell_table` unisce, per campione,
la rilevazione, ``n_sites`` del campione marcato (per gli attaccati quello del genitore; per
MCGMark sono le posizioni marcate), l'esito dell'esecuzione e ``loc_to_generate`` del problema.
:func:`band_rows` raggruppa i campioni per fascia di una colonna e calcola per fascia ``n``,
``tpr_at_fpr`` (soglia della cella), ``message_accuracy`` (tutti i bit giusti; rilevazione non
``OK`` = 0 bit, come la fase ``metrics``), ``bit_accuracy`` e ``pass_at_1``: proporzioni con l'IC
di Wilson, ``bit_accuracy`` con il bootstrap per problema della fase ``metrics``. I campioni con
valore 0, mancante o fuori dalle fasce vanno in una riga a parte (``<colonna>=0/none``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import pandas as pd

from bench.metrics.calibration import detected
from bench.metrics.stats import ratio_interval, wilson

Band = tuple[int, int | None]

# Fasce di siti di §3.10.3 e della fascia D32 di MCSS (metodi post-hoc, controlli su dev).
SITE_BANDS: tuple[Band, ...] = ((1, 7), (8, 12), (13, 19), (20, 31), (32, 55), (56, None))
# MCGMark: fasce a giri di 24 posizioni (12 d'informazione + 12 di correzione); sotto le 24
# posizioni non c'è un giro completo e il messaggio non si legge (decisione dell'utente del 28
# novembre 2026).
MCGMARK_SITE_BANDS: tuple[Band, ...] = ((1, 23), (24, 47), (48, 95), (96, None))
# Fasce di ``loc_to_generate`` dell'inventario.
LOC_BANDS: tuple[Band, ...] = ((1, 5), (6, 10), (11, 20), (21, 40), (41, None))
N_RESAMPLES = 1000


def band_label(band: Band) -> str:
    low, high = band
    return f"{low}+" if high is None else f"{low}-{high}"


def band_of(value: Any, bands: Sequence[Band]) -> str | None:
    """Etichetta della fascia chiusa che contiene ``value`` (``None`` per 0, mancante o fuori)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    v = int(value)
    for low, high in bands:
        if v >= low and (high is None or v <= high):
            return band_label((low, high))
    return None


def band_rows(
    table: pd.DataFrame,
    column: str,
    bands: Sequence[Band],
    message_bits: int,
    tau: float,
    seed: int,
) -> list[dict[str, Any]]:
    """Righe della tabella per fascia di ``column`` (vedi la docstring del modulo); l'ultima è
    quella dei campioni fuori dalle fasce."""
    t = table.copy()
    t["band"] = [band_of(v, bands) for v in t[column]]
    t["hit"] = [
        detected(None if pd.isna(s) else float(s), str(st), None if pd.isna(e) else str(e), tau)
        for s, st, e in zip(t["score"], t["status"], t["embed_status"], strict=True)
    ]
    bits = pd.to_numeric(t["bits_correct"], errors="coerce").fillna(0).where(t["status"] == "OK", 0)
    t["full"] = bits == message_bits
    t["bit_frac"] = bits / message_bits
    t["passed"] = t["exec_status"] == "PASSED"
    rows: list[dict[str, Any]] = []
    labels: list[str | None] = [band_label(b) for b in bands] + [None]
    for label in labels:
        sel = t[t["band"].isna()] if label is None else t[t["band"] == label]
        n = len(sel)
        row: dict[str, Any] = {"band": label or f"{column}=0/none", "n": n}
        for name, col in (("tpr_at_fpr", "hit"), ("message_accuracy", "full"),
                          ("pass_at_1", "passed")):  # fmt: skip
            k = int(sel[col].sum())
            low, high = wilson(k, n)
            row.update({name: k / n if n else math.nan, f"{name}_low": low, f"{name}_high": high})
        row["bit_accuracy"] = float(sel["bit_frac"].mean()) if n else math.nan
        low, high = ratio_interval(sel, "bit_frac", seed, N_RESAMPLES) if n else (math.nan,) * 2
        row.update(bit_accuracy_low=low, bit_accuracy_high=high)
        rows.append(row)
    return rows


def cell_table(store: Any, info: dict[str, Any]) -> pd.DataFrame:
    """Rilevazioni della cella con ``n_sites``, esito dell'esecuzione e ``loc_to_generate``.

    ``info``: ``method``, ``model_id``, ``config_hash``, ``level``, ``language``, ``split`` e, per
    una cella con attacco, ``attack_id`` e ``attack_params_hash``."""
    from bench.pipeline.stage import Cell
    from bench.pipeline.stages.attack import attacked_ref, attacked_subject
    from bench.pipeline.stages.detect import detection_ref
    from bench.pipeline.stages.execute import execution_ref
    from bench.pipeline.stages.generate_baseline import problems_ref
    from bench.pipeline.stages.watermark import watermarked_ref

    method, model, h = info["method"], info["model_id"], info["config_hash"]
    level, lang, split = info["level"], info["language"], info["split"]
    attack, params = info.get("attack_id"), str(info.get("attack_params_hash"))
    subject = attacked_subject(str(attack), params) if attack else "positives"
    det = store.read_table(detection_ref(method, model, h, subject, level, lang, split))
    det = det[det["status"] != "NOT_APPLICABLE"]
    marked = store.read_table(watermarked_ref(method, model, h, level, lang, split))
    sites = dict(zip(marked["sample_id"], marked["n_sites"], strict=True))
    if attack:
        children = store.read_table(
            attacked_ref(str(attack), params, method, model, h, level, lang, split)
        )
        parent = dict(zip(children["sample_id"], children["parent_id"], strict=True))
        det = det.assign(n_sites=[sites.get(parent.get(s)) for s in det["sample_id"]])
    else:
        det = det.assign(n_sites=[sites.get(s) for s in det["sample_id"]])
    source = "attacked" if attack else "llm_watermarked"
    cell = Cell(source=source, method=method, model_id=model, level=level, language=lang,
                split=split, attack_id=attack, attack_params_hash=params)  # fmt: skip
    exe = store.read_table(execution_ref(cell, h))
    status = dict(zip(exe["sample_id"], exe["status"], strict=True))
    problems = store.read_table(problems_ref(level, lang))
    loc = dict(zip(problems["problem_key"], problems["loc_to_generate"], strict=True))
    out: pd.DataFrame = det.assign(
        exec_status=[status.get(s) for s in det["sample_id"]],
        loc_to_generate=[loc.get(p) for p in det["problem_key"]],
    )
    return out
