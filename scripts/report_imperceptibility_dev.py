"""Impercettibilità su sviluppo di mcss e multimark (pipeline di multimark v1.3.5).

Legge le uscite delle fasi dell'impercettibilità (ADR-015; sweep su sviluppo, ADR-017 §7) per le
configurazioni ``--config metodo:hash[:etichetta]`` su L1 e L2 dev e per ogni modello, e stampa:

1. **Classificatori e misure accoppiate** (``metrics_imperceptibility``):
   - ``adversarial_logreg``: AUROC out-of-fold con IC (misura del **vincolo** di v1.3.3: AUROC non
     peggiore di 0,10 rispetto a ``multimark`` base, ``--base``) e differenza dalla base;
   - ``adversarial_unixcoder``: stessa misura in una colonna separata, **secondaria** (non entra
     nel vincolo);
   - per entrambi, l'AUROC sui soli marcati con codice cambiato (``chg``), dalle stesse predizioni
     out-of-fold, senza riaddestrare (un inserimento ``FAILED`` lascia il codice della baseline e
     sposta l'AUROC verso 0,5); senza intervallo;
   - CodeBLEU (contro ogni baseline dello stesso problema), CodeBLEU − variabilità naturale,
     CodeBLEU esatta (contro il campione da cui deriva), ΔNLL (differenza delle medie per problema
     ed esatta).
2. **Modifiche al codice** (ogni marcato contro il campione della baseline da cui deriva,
   ``parent_id``), su tutti i campioni e sui soli campioni con codice cambiato:
   - frazione di identificatori distinti dell'originale (nodi ``identifier`` di tree-sitter) che
     non compaiono più nel marcato (campioni senza identificatori esclusi da questa media);
   - numero medio assoluto di identificatori cambiati per campione, accanto ai nomi rinominati
     per campione dello spareggio (``n_renamed`` dell'inserimento, ``report_round3``);
   - distanza di Levenshtein media sui caratteri e normalizzata sulla lunghezza dell'originale.
   Gli inserimenti falliti hanno il codice della baseline: contano 0. Campioni con codice vuoto
   esclusi (D37).
3. **Vincolo di v1.3.3** per configurazione: massimo su (modello, livello) della differenza di AUROC
   di ``adversarial_logreg`` dalla base, ``ok`` se ≤ 0,10; per la decisione R anche la differenza
   del punto ``--r-point`` su L2 (limite 0,05).

Gira in **bench-core**, solo lettura, solo sviluppo::

    python scripts/report_imperceptibility_dev.py --config mcss:<hash>:mcss \\
        --config multimark:<hash>:multimark ... [--levels L1 L2] [--report FILE] [override ...]

``--report`` non deve esistere (i log non si sovrascrivono); ``--split test`` non è ammesso.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import socket
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
LOGREG_MAX_DELTA = 0.10  # vincolo di v1.3.3
R_MAX_DELTA = 0.05  # decisione R (§7 della pipeline)
VARIANTS = (("logreg", "constraint"), ("unixcoder", "secondary"))
PAIRED = ("codebleu", "codebleu_minus_natural", "codebleu_exact", "delta_nll_mean",
          "delta_nll_exact")  # fmt: skip
EDIT_COLS = ("ident_frac", "ident_abs", "lev", "lev_norm")


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
Config = r3.rad.Config
_v = r3.rad._v


def levenshtein(a: str, b: str) -> int:
    """Distanza di Levenshtein sui caratteri, algoritmo bit-parallelo di Myers/Hyyrö con gli
    interi di Python (righe intere in un intero): O(len(a) · len(b) / 64) in pratica."""
    if a == b:
        return 0
    start = 0  # prefisso e suffisso comuni non cambiano la distanza
    while start < min(len(a), len(b)) and a[start] == b[start]:
        start += 1
    end = 0
    while end < min(len(a), len(b)) - start and a[-1 - end] == b[-1 - end]:
        end += 1
    a, b = a[start : len(a) - end], b[start : len(b) - end]
    if len(a) < len(b):
        a, b = b, a
    m = len(b)
    if m == 0:
        return len(a)
    peq: dict[str, int] = {}
    for i, ch in enumerate(b):
        peq[ch] = peq.get(ch, 0) | (1 << i)
    mask = (1 << m) - 1
    high = 1 << (m - 1)
    pv, mv, score = mask, 0, m
    for ch in a:
        eq = peq.get(ch, 0)
        xv = eq | mv
        xh = (((eq & pv) + pv) ^ pv) | eq
        ph = mv | (~(xh | pv) & mask)
        mh = pv & xh
        if ph & high:
            score += 1
        elif mh & high:
            score -= 1
        ph = ((ph << 1) | 1) & mask
        mh = (mh << 1) & mask
        pv = mh | (~(xv | ph) & mask)
        mv = ph & xv
    return score


def identifiers(code: str, language: str, parsers: Any = None) -> set[str]:
    """Nomi distinti dei nodi ``identifier`` di tree-sitter."""
    from bench.domain.enums import Language
    from bench.lang.parsers import default_parsers

    parsers = parsers or default_parsers()
    stack = [parsers.parse(code, Language(language)).root_node]
    names: set[str] = set()
    while stack:
        node = stack.pop()
        if node.type == "identifier" and node.text is not None:
            names.add(node.text.decode("utf-8", errors="replace"))
        stack.extend(node.children)
    return names


def edit_table(marked: pd.DataFrame, baseline: pd.DataFrame, language: str) -> pd.DataFrame:
    """Per ogni marcato non vuoto con l'originale non vuoto: ``changed``, misure di
    ``EDIT_COLS`` (``ident_frac`` NaN se l'originale non ha identificatori)."""
    from bench.metrics.imperceptibility import is_empty

    original: dict[str, Any] = {
        str(k): v for k, v in zip(baseline["sample_id"], baseline["code"], strict=True)
    }
    rows = []
    for sid, parent, raw in zip(marked["sample_id"], marked["parent_id"], marked["code"],
                                strict=True):  # fmt: skip
        found = original.get(str(parent))
        if is_empty(raw) or is_empty(found):
            continue
        code, orig = str(raw), str(found)
        before = identifiers(orig, language)
        lost = len(before - identifiers(code, language)) if code != orig else 0
        dist = levenshtein(orig, code)
        rows.append({
            "sample_id": str(sid),
            "changed": code != orig,
            "ident_frac": lost / len(before) if before else math.nan,
            "ident_abs": float(lost),
            "lev": float(dist),
            "lev_norm": dist / max(1, len(orig)),
        })  # fmt: skip
    return pd.DataFrame(rows, columns=["sample_id", "changed", *EDIT_COLS])


def edit_summary(table: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Medie di ``EDIT_COLS`` su tutti i campioni (``all``) e sui soli cambiati (``changed``)."""
    out: dict[str, dict[str, float]] = {}
    for label, part in (("all", table), ("changed", table[table["changed"].astype(bool)])):
        row: dict[str, float] = {"n": float(len(part))}
        for col in EDIT_COLS:
            vals = part[col].dropna()
            row[col] = float(vals.mean()) if len(vals) else math.nan
        out[label] = row
    return out


def changed_auroc(oof: pd.DataFrame, changed: set[str]) -> float:
    """AUROC out-of-fold con i soli positivi a codice cambiato (negativi tutti)."""
    from bench.metrics.stats import auroc

    pos = oof[(oof["label"] == 1) & oof["sample_id"].astype(str).isin(changed)]["score"]
    neg = oof[oof["label"] == 0]["score"]
    if not len(pos) or not len(neg):
        return math.nan
    return float(auroc(pos.to_numpy(), neg.to_numpy()))


class Reader:
    """Artefatti di una cella (configurazione, modello, livello)."""

    def __init__(self, cfg: Any) -> None:
        from bench.store.artifact_store import ArtifactStore

        self.cfg = cfg
        self.store = ArtifactStore(Path(cfg.paths.artifacts))

    def cell(self, c: Any, model: str, level: str, lang: str, split: str) -> dict[str, Any]:
        """Misure di una cella; chiavi mancanti se l'artefatto non c'è."""
        from bench.pipeline.stages.generate_baseline import baseline_ref
        from bench.pipeline.stages.imperceptibility import adversarial_ref, imperceptibility_ref
        from bench.pipeline.stages.watermark import watermarked_ref

        out: dict[str, Any] = {}
        h = c.config_hash
        ref = imperceptibility_ref(c.method, model, h, level, lang, split)
        if self.store.exists(ref):
            t = self.store.read_table(ref)
            out["metrics"] = {r["name"]: r for r in t.to_dict(orient="records")}
        marked_ref = watermarked_ref(c.method, model, h, level, lang, split)
        if self.store.exists(marked_ref):
            marked = self.store.read_table(marked_ref)
            base = self.store.read_table(baseline_ref(model, level, lang, split))
            edits = edit_table(marked, base, lang)
            out["edits"] = edit_summary(edits)
            changed = set(edits.loc[edits["changed"].astype(bool), "sample_id"])
            for variant, _ in VARIANTS:
                oref = adversarial_ref(variant, c.method, model, h, "baseline", level, lang, split)
                if self.store.exists(oref):
                    out[f"chg_{variant}"] = changed_auroc(self.store.read_table(oref), changed)
        root = Path(self.cfg.paths.artifacts) / "_runs" / "watermark"
        mean, _ = r3.renames_per_sample(root / c.method / model / h / f"{level}_{lang}_{split}")
        out["renames"] = mean
        return out


def _metric(d: dict[str, Any], name: str) -> float:
    row = d.get("metrics", {}).get(name)
    return math.nan if row is None or row["value"] is None else float(row["value"])


def _ci(d: dict[str, Any], name: str) -> str:
    row = d.get("metrics", {}).get(name)
    if row is None:
        return "-"
    return f"{_v(row['value'])} [{_v(row['ci_low'])},{_v(row['ci_high'])}]"


def cell_lines(configs: Sequence[Any], data: dict[str, dict[str, Any]], base: str, model: str,
               level: str) -> list[str]:  # fmt: skip
    """Tabelle di una cella (modello, livello)."""
    base_auc = {v: _metric(data.get(base, {}), f"adversarial_{v}_auroc_oof") for v, _ in VARIANTS}
    out = [f"## {model} {level}", "### classifiers (AUROC out-of-fold; chg = changed code only)",
           f"  {'config':<34}{'logreg [CI] (constraint)':>28}{'Δbase':>8}{'chg':>8}"
           f"   |{'unixcoder [CI] (SECONDARY)':>30}{'Δbase':>8}{'chg':>8}"]  # fmt: skip
    for c in configs:
        d = data.get(c.name, {})
        parts = []
        for variant, _ in VARIANTS:
            name = f"adversarial_{variant}_auroc_oof"
            auc = _metric(d, name)
            parts.append(
                f"{_ci(d, name):>{28 if variant == 'logreg' else 30}}"
                f"{r3.rad._d(auc, base_auc[variant]):>8}{_v(d.get(f'chg_{variant}', math.nan)):>8}"
            )
        out.append(f"  {c.name:<34}{parts[0]}   |{parts[1]}")
    out += ["### paired measures (metrics_imperceptibility)",
            f"  {'config':<34}" + "".join(f"{n:>24}" for n in PAIRED)]  # fmt: skip
    for c in configs:
        d = data.get(c.name, {})
        out.append(f"  {c.name:<34}" + "".join(f"{_v(_metric(d, n)):>24}" for n in PAIRED))
    out += ["### code edits vs the parent baseline sample (all samples | changed code only)",
            f"  {'config':<34}{'n':>6}" + "".join(f"{x:>10}" for x in EDIT_COLS)
            + f"{'renames':>9}   |{'n':>6}" + "".join(f"{x:>10}" for x in EDIT_COLS)]  # fmt: skip
    for c in configs:
        e = data.get(c.name, {}).get("edits")
        if e is None:
            out.append(f"  {c.name:<34}no marked samples")
            continue
        ren = data[c.name].get("renames", math.nan)
        a, ch = e["all"], e["changed"]
        out.append(f"  {c.name:<34}{int(a['n']):>6}" + "".join(f"{_v(a[x]):>10}" for x in EDIT_COLS)
                   + f"{_v(ren):>9}   |{int(ch['n']):>6}"
                   + "".join(f"{_v(ch[x]):>10}" for x in EDIT_COLS))  # fmt: skip
    return [*out, ""]


def constraint_lines(configs: Sequence[Any], cells: dict[tuple[str, str], dict[str, Any]],
                     base: str, r_point: str | None) -> list[str]:  # fmt: skip
    """Vincolo di v1.3.3 su ``adversarial_logreg`` e decisione R (sezione 3)."""
    name = "adversarial_logreg_auroc_oof"
    out = ["## constraint of v1.3.3: adversarial_logreg AUROC <= base + "
           f"{LOGREG_MAX_DELTA:g} on every cell (unixcoder is secondary, not in the constraint)",
           f"  {'config':<34}{'max Δ':>8}{'cells':>8}  per cell"]  # fmt: skip
    for c in configs:
        deltas = {}
        for (model, level), data in cells.items():
            deltas[f"{model} {level}"] = _metric(data.get(c.name, {}), name) - _metric(
                data.get(base, {}), name
            )
        vals = list(deltas.values())
        worst = math.nan if not vals or any(math.isnan(x) for x in vals) else max(vals)
        ok = "-" if math.isnan(worst) else ("ok" if worst <= LOGREG_MAX_DELTA else "FAIL")
        per = " ".join(f"{k} {r3.rad._d(v, 0.0)}" for k, v in deltas.items())
        out.append(f"  {c.name:<34}{_v(worst):>8}{ok:>8}  {per}")
    if r_point is not None:
        point = next((c for c in configs if c.label == r_point), None)
        if point is not None:
            l2 = {k: v for k, v in cells.items() if k[1] == "L2"}
            diffs = {
                m: r3.rad._d(_metric(d.get(point.name, {}), name), _metric(d.get(base, {}), name))
                for (m, _), d in l2.items()
            }
            per = " ".join(f"{m} {x}" for m, x in diffs.items())
            out.append(
                f"  decision R ({point.name} vs base, L2 dev, limit +{R_MAX_DELTA:g}): {per}"
            )
    return [*out, ""]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument("--base", default="multimark", help="label of the reference configuration")
    p.add_argument("--r-point", default="single_letter", help="label for decision R (L2)")
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.split == "test":
        print("[ERROR] development measure only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1
    base = next((c for c in args.config if c.label == args.base), None)
    if base is None:
        print(f"[ERROR] --base {args.base}: not among the labels", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics_imperceptibility", *args.overrides])
    reader = Reader(cfg)
    lines = [
        "# imperceptibility on development (scripts/report_imperceptibility_dev.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# configs: {', '.join(c.name for c in args.config)}; base {base.name}",
        f"# split {args.split}, language {args.language}; classifiers trained out-of-fold within "
        "each dev cell (5 folds grouped by problem), nothing trained or read on test",
        "",
    ]
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for model in args.models or list(cfg.models):
        for level in args.levels:
            data = {c.name: reader.cell(c, model, level, args.language, args.split)
                    for c in args.config}  # fmt: skip
            cells[(model, level)] = data
            lines += cell_lines(args.config, data, base.name, model, level)
    lines += constraint_lines(args.config, cells, base.name, args.r_point)
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
