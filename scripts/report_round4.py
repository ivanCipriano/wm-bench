"""Giro 4 su sviluppo (pipeline di multimark v1.3.9 e v1.3.10): i due punti di lavoro.

Per ogni configurazione (``--config metodo:hash[:etichetta]``: i 12 punti del giro 4, ``mcss``
``ae63f16de28f`` e le 7 configurazioni del giro di capacità come riferimento), su L1 e L2 dev e per
ogni modello, lo script legge gli artefatti già prodotti e stampa:

1. **Configurazione prudente** (criterio di v1.3.3, aggregazione di v1.3.4): media su S del TPR
   alla soglia calibrata (``report_round3``: ogni condizione è la media semplice delle quattro
   celle modello × livello). Vincoli: ``ΔPass@1`` ≤ 3 pp su ogni cella senza attacco; FPR su
   codice umano alla soglia teorica ``score >= 2`` ≤ 1% su ogni livello; AUROC di
   ``adversarial_logreg`` ≤ base (``--base``, ``3c1bd11758fc``) + 0,10 su ogni cella; rinomine
   incoerenti ≤ 1% per cella (v1.3.9, fra i vincoli di v1.3.3 secondo v1.3.10). Si stampa anche
   la classifica senza il vincolo delle rinomine, se cambia il primo posto.
2. **Configurazione ad alta capacità** (regola ufficiale di v1.3.10): fra i punti che rispettano
   ``ΔPass@1``, FPR e rinomine, quelli con AUROC di ``adversarial_logreg`` **minore** di quella di
   ``mcss`` (``--mcss``) in ogni cella (stima puntuale; gli intervalli si riportano); vince la
   media su S più alta. Parità entro 0,01 come in v1.3.4: meno rinomine per campione.
   In entrambe le classifiche entrano solo le configurazioni di ``multimark``; ``mcss`` è il
   riferimento. Una misura mancante conta come vincolo non rispettato (``incomplete``).
3. **Ripiego di ``names.suffix_by_type``** (MD30): per cella, somma di ``n_names_type_fallback``
   (nomi scelti dall'elenco completo perché nessun candidato filtrato per tipo aveva i bit giusti)
   negli ``extra`` dell'inserimento, campioni con almeno un ripiego, e quota sui nomi rinominati
   (``n_renamed``); ``-`` per le configurazioni senza l'opzione (campo assente).
4. **Siti medi** per configurazione e cella, dagli ``extra`` dell'inserimento
   (``_runs/watermark/.../results.jsonl``): ``n_sites`` (bit scritti), ``n_structural_sites`` e
   ``n_name_sites`` medi su tutti i campioni con il campo (inserimenti falliti compresi).

Rinomine incoerenti: ``multimark.rename_check.check_renaming`` (``scripts/check_rename_dev.py``)
sui marcati senza attacco contro il campione della baseline da cui derivano, quota dei campioni
con violazioni sui controllati; la libreria si importa da ``--multimark`` (commit che deve
cominciare con ``--expect-commit``).

Gira in **bench-core**, solo lettura, solo sviluppo::

    python scripts/report_round4.py --config mcss:<hash>:mcss_new ... \\
        --config multimark:<hash>:r4_B ... [--base multimark] [--mcss mcss_new] \\
        [--multimark DIR] [--expect-commit SHA] [--levels L1 L2] [--report FILE] [override ...]

``--report`` non deve esistere (i log non si sovrascrivono); ``--split test`` non è ammesso.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LOGREG = "adversarial_logreg_auroc_oof"
LOGREG_MAX_DELTA = 0.10  # vincolo di v1.3.3
RENAME_MAX = 0.01  # v1.3.9: rinomine incoerenti ≤ 1% per cella
SITE_FIELDS = ("n_sites", "n_structural_sites", "n_name_sites")


def _load(name: str) -> Any:
    """Uno script di ``scripts/`` come modulo (registrato in ``sys.modules`` per le dataclass)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


r3 = _load("report_round3")
crd = _load("check_rename_dev")
rad = r3.rad
Config = rad.Config
_v = rad._v


def _pct(x: float) -> str:
    return "-" if math.isnan(x) else f"{100 * x:.2f}%"


# ---------------------------------------------------------------------- estrazioni per cella
def embed_extras(run_dir: Path) -> list[dict[str, Any]]:
    """(stato, extra) dell'inserimento per campione, da ``results.jsonl`` (vuota se manca)."""
    path = run_dir / "results.jsonl"
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                rows.append({"status": row.get("status"), **(row.get("extra") or {})})
    return rows


def fallback_stats(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Ripiego di ``suffix_by_type``: ``n`` campioni, ``with_field`` (campioni con il campo),
    ``total`` ripieghi, ``samples`` con almeno un ripiego, ``renamed`` nomi rinominati e
    ``share`` = total / renamed (NaN se il campo non c'è mai: opzione spenta)."""
    with_field = [r for r in rows if "n_names_type_fallback" in r]
    total = sum(int(r.get("n_names_type_fallback") or 0) for r in with_field)
    renamed = sum(int(r.get("n_renamed") or 0) for r in with_field)
    return {
        "n": float(len(rows)),
        "with_field": float(len(with_field)),
        "total": float(total) if with_field else math.nan,
        "samples": float(sum(1 for r in with_field if (r.get("n_names_type_fallback") or 0) > 0))
        if with_field else math.nan,
        "renamed": float(renamed) if with_field else math.nan,
        "share": total / renamed if with_field and renamed else math.nan,
    }  # fmt: skip


def site_stats(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Medie di ``SITE_FIELDS`` sui campioni con il campo; ``n`` = campioni."""
    out: dict[str, float] = {"n": float(len(rows))}
    for name in SITE_FIELDS:
        vals = [float(r[name]) for r in rows if r.get(name) is not None]
        out[name] = sum(vals) / len(vals) if vals else math.nan
    return out


def logreg_cell(store: Any, c: Any, model: str, level: str, lang: str, split: str
                ) -> tuple[float, float, float]:  # fmt: skip
    """AUROC out-of-fold di ``adversarial_logreg`` con IC (``metrics_imperceptibility``)."""
    from bench.pipeline.stages.imperceptibility import imperceptibility_ref

    ref = imperceptibility_ref(c.method, model, c.config_hash, level, lang, split)
    if not store.exists(ref):
        return math.nan, math.nan, math.nan
    t = store.read_table(ref)
    rows = t[t["name"] == LOGREG]
    if not len(rows):
        return math.nan, math.nan, math.nan
    row = rows.iloc[0]

    def num(x: Any) -> float:
        return math.nan if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)

    return num(row["value"]), num(row.get("ci_low")), num(row.get("ci_high"))


# ---------------------------------------------------------------------- dati per configurazione
@dataclass
class ConfigData:
    """Misure di una configurazione sulle celle (modello, livello)."""

    config: Any
    mean_s: float = math.nan
    dp_max: float = math.nan
    fprs: dict[str, float] = field(default_factory=dict)
    renames: float = math.nan
    logreg: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    rename_rate: dict[str, float] = field(default_factory=dict)
    fallback: dict[str, dict[str, float]] = field(default_factory=dict)
    sites: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def ok_dp(self) -> bool:
        return not math.isnan(self.dp_max) and self.dp_max <= r3.DELTA_PASS_MAX_PP

    @property
    def ok_fpr(self) -> bool:
        vals = list(self.fprs.values())
        return bool(vals) and all(not math.isnan(f) and f <= r3.FPR_MAX for f in vals)

    @property
    def rename_max(self) -> float:
        vals = list(self.rename_rate.values())
        return math.nan if not vals or any(math.isnan(x) for x in vals) else max(vals)

    @property
    def ok_rename(self) -> bool:
        return not math.isnan(self.rename_max) and self.rename_max <= RENAME_MAX


def logreg_vs(d: ConfigData, ref: ConfigData, cells: Sequence[str]) -> dict[str, float]:
    """Differenza di AUROC logreg da ``ref`` per cella (NaN se manca una delle due)."""
    out = {}
    for cell in cells:
        a, b = d.logreg.get(cell, (math.nan,))[0], ref.logreg.get(cell, (math.nan,))[0]
        out[cell] = math.nan if math.isnan(a) or math.isnan(b) else a - b
    return out


def ok_prudent_logreg(d: ConfigData, base: ConfigData, cells: Sequence[str]) -> bool:
    deltas = list(logreg_vs(d, base, cells).values())
    return bool(deltas) and all(not math.isnan(x) and x <= LOGREG_MAX_DELTA for x in deltas)


def ok_below_mcss(d: ConfigData, mcss: ConfigData, cells: Sequence[str]) -> bool:
    deltas = list(logreg_vs(d, mcss, cells).values())
    return bool(deltas) and all(not math.isnan(x) and x < 0 for x in deltas)


def rank(data: Sequence[ConfigData], valid: Callable[[ConfigData], bool]
         ) -> list[tuple[Any, float, float, bool]]:  # fmt: skip
    """Classifica di ``report_round3.ranking`` sulle configurazioni che rispettano ``valid``."""
    rows: list[tuple[Any, ...]] = [
        (d.config, d.mean_s, [], d.dp_max, d.fprs, valid(d), d.renames) for d in data
    ]
    return list(r3.ranking(rows))


def ranking_lines(title: str, ranked: Sequence[tuple[Any, float, float, bool]]) -> list[str]:
    out = [title]
    for i, (c, mean, ren, tied) in enumerate(ranked, 1):
        note = "  [tie group: fewer renames wins]" if tied else ""
        out.append(f"    {i}. {c.name}  mean_S {_v(mean)}  renames/sample {_v(ren)}{note}")
    if not ranked:
        out.append("    none")
    return out


# ---------------------------------------------------------------------- report
class Round4:
    """Raccoglie le misure (``report_round3.Round3`` per S, ΔPass@1, FPR e rinomine)."""

    def __init__(self, rep: Any, configs: Sequence[Any], check: Callable[[str, str], Any],
                 language: str) -> None:  # fmt: skip
        self.rep = rep
        self.configs = list(configs)
        self.check = check
        self.r3 = r3.Round3(rep, configs, r3.s_points(language))
        self.data = {c.name: ConfigData(c) for c in configs}
        self.cells: list[str] = []

    def collect(self, models: Sequence[str], levels: Sequence[str], lang: str, split: str) -> None:
        for model in models:
            for level in levels:
                cell = f"{model} {level}"
                self.cells.append(cell)
                self.r3.cell_block(model, level, lang, split)  # accumula S, ΔPass@1, FPR
                for c in self.configs:
                    d = self.data[c.name]
                    d.logreg[cell] = logreg_cell(self.rep.store, c, model, level, lang, split)
                    try:
                        cell_data = crd.read_cell(self.rep.store, c, model, level, lang, split)
                        d.rename_rate[cell] = crd.cell_stats(*cell_data, self.check, 0)["rate"]
                    except Exception:  # marcati mancanti: misura assente
                        d.rename_rate[cell] = math.nan
                    rows = embed_extras(self.r3.run_dir(c, model, level, lang, split))
                    d.fallback[cell] = fallback_stats(rows)
                    d.sites[cell] = site_stats(rows)
        for c in self.configs:
            d = self.data[c.name]
            rates = [self.r3.pooled[c.name][cond].rate for cond in self.r3.conditions]
            d.mean_s = math.nan if any(math.isnan(r) for r in rates) else sum(rates) / len(rates)
            dps = list(self.r3.delta_pass[c.name].values())
            d.dp_max = math.nan if not dps or any(math.isnan(x) for x in dps) else max(dps)
            for level, vals in sorted(self.r3.fpr[c.name].items()):
                k, n = sum(v[1] for v in vals), sum(v[2] for v in vals)
                d.fprs[level] = k / n if n else math.nan
            d.renames = self.r3.renames[c.name].rate

    def lines(self, base: Any, mcss: Any) -> list[str]:
        """Vincoli per configurazione, AUROC logreg e rinomine incoerenti per cella."""
        b, m = self.data[base.name], self.data[mcss.name]
        levels = sorted({lv for d in self.data.values() for lv in d.fprs})
        out = ["## constraints per configuration",
               "# max ΔPass@1 (pp) over the cells; FPR_2 = FPR on human code at score >= "
               f"{r3.THEORETICAL_THRESHOLD:g} per level; logreg Δbase = max over the cells of the "
               f"AUROC difference from {base.name} (limit +{LOGREG_MAX_DELTA:g}); <mcss = logreg "
               f"AUROC below {mcss.name} in every cell; rename = max share of samples with "
               f"inconsistent renaming over the cells (limit {RENAME_MAX:.0%})",
               "  " + f"{'config':<34}{'mean_S':>8}{'ΔPass':>8}"
               + "".join(f"{'FPR_2 ' + lv:>12}" for lv in levels)
               + f"{'Δbase':>8}{'<mcss':>7}{'rename':>9}{'ren/smp':>9}"]  # fmt: skip
        for d in self.data.values():
            deltas = list(logreg_vs(d, b, self.cells).values())
            worst = math.nan if not deltas or any(math.isnan(x) for x in deltas) else max(deltas)
            below = "yes" if ok_below_mcss(d, m, self.cells) else "no"
            out.append(
                f"  {d.config.name:<34}{_v(d.mean_s):>8}{_v(d.dp_max):>8}"
                + "".join(f"{_v(d.fprs.get(lv, math.nan)):>12}" for lv in levels)
                + f"{_v(worst):>8}{('-' if d is m else below):>7}{_pct(d.rename_max):>9}"
                f"{_v(d.renames):>9}"
            )
        out += ["", "## adversarial_logreg AUROC per cell: value [CI] (Δbase, Δmcss)"]
        for d in self.data.values():
            dbase, dmcss = logreg_vs(d, b, self.cells), logreg_vs(d, m, self.cells)
            parts = []
            for cell in self.cells:
                val, lo, hi = d.logreg.get(cell, (math.nan, math.nan, math.nan))
                diffs = f"({rad._d(dbase[cell], 0.0)}, {rad._d(dmcss[cell], 0.0)})"
                parts.append(f"{cell} {_v(val)} [{_v(lo)},{_v(hi)}] {diffs}")
            out.append(f"  {d.config.name}: " + "; ".join(parts))
        out += ["", "## inconsistent renaming per cell (share of the checked samples)"]
        for d in self.data.values():
            rates = [f"{cell} {_pct(d.rename_rate.get(cell, math.nan))}" for cell in self.cells]
            out.append(f"  {d.config.name}: " + "; ".join(rates))
        return [*out, ""]

    def ranking_section(self, base: Any, mcss: Any) -> list[str]:
        """Classifiche delle due regole (sezioni 1 e 2 della docstring)."""
        b, m = self.data[base.name], self.data[mcss.name]
        cand = [self.data[c.name] for c in self.configs if c.method == "multimark"]
        out: list[str] = []

        def prudent(d: ConfigData) -> bool:
            return d.ok_dp and d.ok_fpr and d.ok_rename and ok_prudent_logreg(d, b, self.cells)

        def prudent_no_rename(d: ConfigData) -> bool:
            return d.ok_dp and d.ok_fpr and ok_prudent_logreg(d, b, self.cells)

        def high(d: ConfigData) -> bool:
            return d.ok_dp and d.ok_fpr and d.ok_rename and ok_below_mcss(d, m, self.cells)

        r1, r1_nr, r2 = rank(cand, prudent), rank(cand, prudent_no_rename), rank(cand, high)
        out += ranking_lines(
            "## rule 1, prudent configuration (v1.3.3 criterion, v1.3.10): ΔPass@1, FPR_2, "
            f"logreg <= {base.name} + {LOGREG_MAX_DELTA:g} on every cell, renaming <= "
            f"{RENAME_MAX:.0%} per cell; within {r3.TIE} of the best mean_S, fewer renames first",
            r1,
        )
        if [x[0] for x in r1_nr[:1]] != [x[0] for x in r1[:1]]:
            out += ranking_lines("  rule 1 without the renaming constraint (first place differs):",
                                 r1_nr)  # fmt: skip
        out += [""]
        out += ranking_lines(
            "## rule 2, high-capacity configuration (v1.3.10): ΔPass@1, FPR_2, renaming <= "
            f"{RENAME_MAX:.0%} per cell, logreg AUROC < {mcss.name} on every cell (point "
            f"estimate); within {r3.TIE} of the best mean_S, fewer renames first",
            r2,
        )
        winners = {x[0].name for x in (r1[:1] + r2[:1])}
        if r1 and r2:
            out.append("  -> " + ("one working point (the same configuration wins both rules)"
                                  if len(winners) == 1 else "two working points"))  # fmt: skip
        incomplete = [d.config.name for d in cand if math.isnan(d.mean_s)
                      or any(math.isnan(x[0]) for x in d.logreg.values())
                      or math.isnan(d.rename_max)]  # fmt: skip
        if incomplete:
            out.append(f"  incomplete (missing measures count as constraints not met): "
                       f"{', '.join(incomplete)}")  # fmt: skip
        return [*out, ""]

    def extras_section(self) -> list[str]:
        """Ripiego di ``suffix_by_type`` e siti medi (sezioni 3 e 4 della docstring)."""
        out = ["## names.suffix_by_type fallback to the full list (n_names_type_fallback)",
               "  " + f"{'config':<34}{'cell':<30}{'samples':>8}{'fallback':>10}"
               f"{'smp>=1':>8}{'renamed':>9}{'share':>9}"]  # fmt: skip
        for d in self.data.values():
            for cell in self.cells:
                f = d.fallback.get(cell, {})
                if not f or not f.get("with_field"):
                    continue
                widths = (("n", 8), ("total", 10), ("samples", 8), ("renamed", 9))
                counts = "".join(f"{int(f[k]):>{w}}" for k, w in widths)
                out.append(f"  {d.config.name:<34}{cell:<30}{counts}{_pct(f['share']):>9}")
        out.append("  (configurations without the option: field absent, not listed)")
        out += ["", "## mean sites per sample (embedding extras, failed embeddings included)",
                "  " + f"{'config':<34}{'cell':<30}{'samples':>8}"
                + "".join(f"{x:>20}" for x in SITE_FIELDS)]  # fmt: skip
        for d in self.data.values():
            for cell in self.cells:
                s = d.sites.get(cell, {})
                if not s or not s.get("n"):
                    out.append(f"  {d.config.name:<34}{cell:<30}{'no embedding results':>8}")
                    continue
                out.append(f"  {d.config.name:<34}{cell:<30}{int(s['n']):>8}"
                           + "".join(f"{_v(s[x]):>20}" for x in SITE_FIELDS))  # fmt: skip
        return [*out, ""]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument("--base", default="multimark", help="label of the base for rule 1")
    p.add_argument("--mcss", default="mcss_new_3c47a1f", help="label of mcss for rule 2")
    p.add_argument("--multimark", type=Path, default=crd.DEFAULT_MULTIMARK,
                   help="copy of the MultiMark library with multimark/rename_check.py")  # fmt: skip
    p.add_argument("--expect-commit", default="0664817",
                   help="required prefix of the commit of the copy ('' = any)")  # fmt: skip
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--threshold-levels", nargs="+", help="calibration levels (default: --levels)")
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.split == "test":
        print("[ERROR] development round only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1
    by_label = {c.label: c for c in args.config}
    for flag, label in (("--base", args.base), ("--mcss", args.mcss)):
        if label not in by_label:
            print(f"[ERROR] {flag} {label}: not among the labels {list(by_label)}", file=sys.stderr)
            return 1
    try:
        check, commit = crd.load_checker(args.multimark, args.expect_commit)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    t_levels = args.threshold_levels or args.levels
    rep = rad.Reporter(cfg, args.config, t_levels)
    r4 = Round4(rep, args.config, check, args.language)
    r4.collect(args.models or list(cfg.models), args.levels, args.language, args.split)
    points = r4.r3.points
    lines = [
        "# round 4 on development, the two working points of v1.3.10 (scripts/report_round4.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# configs: {', '.join(c.name for c in args.config)}",
        f"# base {by_label[args.base].name}, mcss {by_label[args.mcss].name}; only multimark "
        "configurations are ranked",
        f"# thresholds of levels {' '.join(t_levels)} and theoretical score >= "
        f"{r3.THEORETICAL_THRESHOLD:g}; split {args.split}, language {args.language}",
        f"# S = {r3.NO_ATTACK}; " + "; ".join(f"{q.label} [{q.params_hash}]" for q in points),
        f"# renaming check: multimark.rename_check from {args.multimark} at commit {commit}",
        "",
        *r4.lines(by_label[args.base], by_label[args.mcss]),
        *r4.ranking_section(by_label[args.base], by_label[args.mcss]),
        *r4.extras_section(),
    ]
    text = "\n".join(lines) + "\n"
    if args.report is None:
        sys.stdout.write(text)
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
