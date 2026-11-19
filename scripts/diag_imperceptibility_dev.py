"""Diagnosi di ``adversarial_logreg`` su sviluppo: perché il classificatore riconosce i marcati.

Per ogni configurazione (``--config metodo:hash[:etichetta]``, per esempio ``mcss``, B, C e D del
giro di capacità) e cella dev (modello × livello), solo lettura degli artefatti e calcoli su CPU,
senza scrivere nulla nello store:

a. **Caratteristiche verso "marcato".** Stessi dati della fase ``adversarial_logreg`` (marcati
   non vuoti contro la baseline non vuota dello stesso modello) e stesse caratteristiche
   (``bench.metrics.adversarial``: stile dei nomi e iniziali, istogramma dei nodi tree-sitter,
   n-grammi di token 1–3 TF-IDF con al più 20.000 voci; caratteristiche esplicite scalate con
   ``MaxAbsScaler``). ``C`` si sceglie con la stessa CV interna della fase (3 fold raggruppati per
   problema, griglia ``LOGREG_CS``, AUROC medio) ma **su tutta la cella**, con il seme dei fold
   della fase; poi un modello su tutta la cella. Si stampano le ``--top`` caratteristiche (default
   30) con il coefficiente più alto (verso "marcato"), con il tipo (``name``: stile e iniziali dei
   nomi; ``node``: tipi di nodo; ``ngram``: n-grammi) e il valore medio nei marcati e nella
   baseline (frazione per ``name``/``node``, TF-IDF per ``ngram``). Si riportano anche le ``C``
   scelte dalla fase per ogni fold (manifest). Poi, per configurazione, le caratteristiche che
   stanno fra le prime in almeno 3 delle 4 celle.
b. **Campioni riconosciuti con più sicurezza.** I ``--samples`` marcati (default 5) con il
   punteggio out-of-fold più alto nelle predizioni della fase (le stesse dell'AUROC del report),
   con il diff unificato contro il campione della baseline da cui derivano (``parent_id``).
c. **Suffissi dei nomi nuovi** (configurazioni ``multimark`` e ``--suffix-configs``, default B, C,
   D): nomi nuovi = identificatori del marcato assenti dal genitore (nodi ``identifier`` di
   tree-sitter, esclusi builtin e parole chiave), sui campioni con codice cambiato. Frazione che
   finisce con ``_<s>`` per ``s`` in ``EXTRA_SUFFIXES`` della libreria, con una cifra, con l'uno
   o l'altro; per confronto con i suffissi di base (``SUFFIXES`` e ``TYPE_SUFFIXES``) e con i
   prefissi (``PREFIXES``); infine gli ultimi segmenti più frequenti.

Gira in **bench-core** (sklearn, come la fase), CPU::

    python scripts/diag_imperceptibility_dev.py --config mcss:<hash>:mcss \\
        --config multimark:<hash>:B ... [--levels L1 L2] [--report FILE] [override Hydra ...]

``--report`` non deve esistere (i log non si sovrascrivono); ``--split test`` non è ammesso.
"""

from __future__ import annotations

import argparse
import builtins
import difflib
import importlib.util
import keyword
import math
import re
import socket
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOP = 30
DEFAULT_SAMPLES = 5
MAX_DIFF_LINES = 80
COMMON_IN = 3  # caratteristiche fra le prime in almeno 3 celle su 4
# Suffissi e prefissi di multimark.names (libreria a4e6972; un test li confronta con la libreria).
EXTRA_SUFFIXES = (
    "data", "num", "obj", "out", "buf", "acc", "elem", "key", "ref", "part", "size", "info",
    "entry", "seq", "base", "step", "last", "orig", "var", "cur", "next", "prev", "total", "new",
)  # fmt: skip
SUFFIXES = ("value", "val", "item", "list", "count", "idx", "tmp", "res")
TYPE_SUFFIXES = ("count", "val", "str", "list", "map", "flag")
PREFIXES = ("cur_", "new_", "the_")
_DIGIT_END = re.compile(r"\d$")
_NOT_NEW = frozenset(dir(builtins)) | frozenset(keyword.kwlist) | frozenset(keyword.softkwlist)


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


rid = _load("report_imperceptibility_dev")
Config = rid.Config


# ---------------------------------------------------------------------- (a) modello sulla cella
class Featurizer:
    """Le caratteristiche di ``bench.metrics.adversarial.logreg_oof`` (``fit_predict``): TF-IDF
    degli n-grammi e caratteristiche esplicite scalate, adattate sui campioni di training."""

    def __init__(self) -> None:
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import MaxAbsScaler

        from bench.metrics.adversarial import MAX_FEATURES, code_tokens

        self.tfidf = TfidfVectorizer(
            tokenizer=code_tokens, lowercase=False, token_pattern=None, ngram_range=(1, 3),
            max_features=MAX_FEATURES,
        )  # fmt: skip
        self.dicts = DictVectorizer()
        self.scaler = MaxAbsScaler()

    def fit_transform(self, codes: Sequence[str], style: Sequence[dict[str, float]]) -> Any:
        from scipy.sparse import hstack

        explicit = self.scaler.fit_transform(self.dicts.fit_transform(style))
        return hstack([self.tfidf.fit_transform(codes), explicit]).tocsr()

    def transform(self, codes: Sequence[str], style: Sequence[dict[str, float]]) -> Any:
        from scipy.sparse import hstack

        explicit = self.scaler.transform(self.dicts.transform(style))
        return hstack([self.tfidf.transform(codes), explicit]).tocsr()

    def names(self) -> list[str]:
        """Nomi delle colonne: n-grammi, poi caratteristiche esplicite."""
        return [*self.tfidf.get_feature_names_out(), *self.dicts.get_feature_names_out()]

    def n_ngrams(self) -> int:
        return len(self.tfidf.get_feature_names_out())


def fit(codes: Sequence[str], style: Sequence[dict[str, float]], labels: Any,
        c: float) -> tuple[Any, Featurizer]:  # fmt: skip
    """Regressione logistica come la fase (``liblinear``, ``max_iter=5000``)."""
    from scipy.sparse import csr_matrix
    from sklearn.linear_model import LogisticRegression

    feats = Featurizer()
    x = feats.fit_transform(codes, style)
    model = LogisticRegression(C=c, max_iter=5000, solver="liblinear")
    model.fit(csr_matrix(x), labels)
    return model, feats


def choose_c(codes: Sequence[str], style: Sequence[dict[str, float]], labels: Any, groups: Any,
             seed: int) -> tuple[float, float]:  # fmt: skip
    """``C`` con la CV interna della fase (``StratifiedGroupKFold(3, shuffle)`` con il seme
    ``seed``, AUROC medio, a parità la ``C`` più piccola): (C, AUROC interno)."""
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedGroupKFold

    from bench.metrics.adversarial import INNER_SPLITS, LOGREG_CS

    labels = np.asarray(labels)
    inner = StratifiedGroupKFold(n_splits=INNER_SPLITS, shuffle=True, random_state=seed)
    best_c, best_auc = LOGREG_CS[0], -1.0
    for c in LOGREG_CS:
        aucs = []
        for itr, ite in inner.split(np.zeros(len(labels)), labels, groups):
            model, feats = fit([codes[i] for i in itr], [style[i] for i in itr], labels[itr], c)
            x = feats.transform([codes[i] for i in ite], [style[i] for i in ite])
            aucs.append(roc_auc_score(labels[ite], model.predict_proba(x)[:, 1]))
        if float(np.mean(aucs)) > best_auc:
            best_c, best_auc = c, float(np.mean(aucs))
    return best_c, best_auc


def kind_of(name: str, n_ngrams: int, index: int) -> str:
    """Tipo di una caratteristica: ``ngram``, ``node`` o ``name`` (stile e iniziali)."""
    if index < n_ngrams:
        return "ngram"
    return "node" if name.startswith("node_") else "name"


def top_features(model: Any, feats: Featurizer, x: Any, labels: Any,
                 top: int) -> list[dict[str, Any]]:  # fmt: skip
    """Le ``top`` caratteristiche con il coefficiente più alto (verso "marcato"), con il valore
    medio nei marcati e nella baseline (valori non scalati per le esplicite)."""
    labels = np.asarray(labels)
    coef = np.asarray(model.coef_[0])
    names = feats.names()
    n_ng = feats.n_ngrams()
    order = np.argsort(-coef)[:top]
    pos, neg = x[labels == 1], x[labels == 0]
    scale = np.ones(len(names))
    scale[n_ng:] = feats.scaler.scale_  # le esplicite sono scalate: si torna ai valori veri
    out = []
    for i in order:
        if coef[i] <= 0:
            break
        out.append({
            "feature": names[i], "kind": kind_of(names[i], n_ng, int(i)), "weight": float(coef[i]),
            "marked": float(pos[:, i].mean()) * scale[i],
            "baseline": float(neg[:, i].mean()) * scale[i],
        })  # fmt: skip
    return out


# ---------------------------------------------------------------------- (b) diff
def diff_lines(before: str, after: str, limit: int = MAX_DIFF_LINES) -> list[str]:
    """Diff unificato baseline → marcato (1 riga di contesto), al più ``limit`` righe."""
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), "baseline",
                                      "marked", n=1, lineterm=""))  # fmt: skip
    if not lines:
        return ["(identical code)"]
    if len(lines) > limit:
        return [*lines[:limit], f"... ({len(lines) - limit} more diff lines)"]
    return lines


# ---------------------------------------------------------------------- (c) suffissi
def new_names(marked: str, parent: str, language: str) -> set[str]:
    """Identificatori del marcato assenti dal genitore (esclusi builtin e parole chiave)."""
    out: set[str] = rid.identifiers(marked, language) - rid.identifiers(parent, language)
    return out - _NOT_NEW


def suffix_kind(name: str) -> dict[str, bool]:
    """Con che cosa finisce (o comincia) un nome nuovo."""
    extra = any(name.endswith(f"_{s}") for s in EXTRA_SUFFIXES)
    digit = bool(_DIGIT_END.search(name))
    return {
        "extra": extra,
        "digit": digit,
        "extra_or_digit": extra or digit,
        "base": any(name.endswith(f"_{s}") for s in (*SUFFIXES, *TYPE_SUFFIXES)),
        "prefix": name.startswith(PREFIXES),
    }


def suffix_stats(pairs: Sequence[tuple[str, str]], language: str) -> dict[str, Any]:
    """Frazioni di :func:`suffix_kind` sui nomi nuovi (uno per campione e nome) delle coppie
    (marcato, genitore) con codice cambiato, e gli ultimi segmenti più frequenti."""
    counts: Counter[str] = Counter()
    last: Counter[str] = Counter()
    n = 0
    for marked, parent in pairs:
        if marked == parent:
            continue
        for name in new_names(marked, parent, language):
            n += 1
            counts.update(k for k, v in suffix_kind(name).items() if v)
            last[name.rsplit("_", 1)[-1] if "_" in name else "(one word)"] += 1
    out: dict[str, Any] = {"n": n, "last": last.most_common(10)}
    for key in ("extra", "digit", "extra_or_digit", "base", "prefix"):
        out[key] = counts[key] / n if n else math.nan
    return out


# ---------------------------------------------------------------------- lettura degli artefatti
class Diagnosis:
    def __init__(self, cfg: Any, top: int, n_samples: int) -> None:
        from bench.store.artifact_store import ArtifactStore

        self.cfg = cfg
        self.store = ArtifactStore(Path(cfg.paths.artifacts))
        self.top = top
        self.n_samples = n_samples
        self.tops: dict[str, list[set[tuple[str, str]]]] = {}

    def data(self, c: Any, model: str, level: str, lang: str, split: str) -> dict[str, Any]:
        """Marcati e baseline non vuoti come nella fase, predizioni out-of-fold e manifest."""
        from bench.metrics import imperceptibility as imp
        from bench.pipeline.stages.generate_baseline import baseline_ref
        from bench.pipeline.stages.imperceptibility import adversarial_ref
        from bench.pipeline.stages.watermark import watermarked_ref

        marked_all = self.store.read_table(
            watermarked_ref(c.method, model, c.config_hash, level, lang, split)
        )
        base_all = self.store.read_table(baseline_ref(model, level, lang, split))
        marked, negatives = imp.non_empty(marked_all), imp.non_empty(base_all)
        data = pd.concat([marked.assign(label=1), negatives.assign(label=0)], ignore_index=True)
        data = data[["sample_id", "problem_key", "code", "label"]]
        oref = adversarial_ref("logreg", c.method, model, c.config_hash, "baseline", level, lang,
                               split)  # fmt: skip
        manifest = self.store.read_manifest(oref)
        return {
            "data": data,
            "marked": marked_all,
            "baseline": base_all,
            "oof": self.store.read_table(oref),
            "fold_log": (manifest.extra.get("fold_log", []) if manifest else []),
        }

    def features_block(self, c: Any, d: dict[str, Any], model: str, lang: str) -> list[str]:
        from bench.domain.enums import Language
        from bench.domain.ids import derive_seed
        from bench.lang.parsers import default_parsers
        from bench.metrics.adversarial import style_features

        data = d["data"]
        codes = [str(x) for x in data["code"]]
        labels = data["label"].to_numpy()
        groups = data["problem_key"].astype(str).to_numpy()
        parsers = default_parsers()
        style = [style_features(x, Language(lang), parsers) for x in codes]
        seed = derive_seed(self.cfg.global_seed, "adversarial-folds", c.method, model, lang,
                           "baseline")  # fmt: skip
        best_c, inner_auc = choose_c(codes, style, labels, groups, seed)
        fitted, feats = fit(codes, style, labels, best_c)
        x = feats.transform(codes, style)
        rows = top_features(fitted, feats, x, labels, self.top)
        stage_cs = ", ".join(f"{r.get('C')}" for r in d["fold_log"]) or "n/d"
        out = [f"  (a) C on the whole cell {best_c:g} (inner AUROC {inner_auc:.3f}); "
               f"C of the stage per fold: {stage_cs}; n = {int(labels.sum())} marked + "
               f"{int((labels == 0).sum())} baseline",
               f"      {'#':>3} {'kind':<6}{'weight':>9}{'marked':>10}{'baseline':>10}"
               "  feature"]  # fmt: skip
        for i, r in enumerate(rows, 1):
            out.append(f"      {i:>3} {r['kind']:<6}{r['weight']:>9.3f}{r['marked']:>10.4f}"
                       f"{r['baseline']:>10.4f}  {r['feature']!r}")  # fmt: skip
        self.tops.setdefault(c.name, []).append({(r["kind"], r["feature"]) for r in rows})
        return out

    def samples_block(self, d: dict[str, Any]) -> list[str]:
        marked, base, oof = d["marked"], d["baseline"], d["oof"]
        parents = dict(zip(marked["sample_id"].astype(str), marked["parent_id"].astype(str),
                           strict=True))  # fmt: skip
        codes = dict(zip(marked["sample_id"].astype(str), marked["code"], strict=True))
        originals = dict(zip(base["sample_id"].astype(str), base["code"], strict=True))
        pos = oof[oof["label"] == 1].sort_values("score", ascending=False).head(self.n_samples)
        out = [f"  (b) {len(pos)} marked samples with the highest out-of-fold score "
               "(adversarial_logreg of the stage)"]  # fmt: skip
        for sid, pkey, score in zip(pos["sample_id"].astype(str), pos["problem_key"],
                                    pos["score"], strict=True):  # fmt: skip
            parent = parents.get(sid, "")
            out.append(f"    --- {pkey} sample {sid[:12]} score {float(score):.4f} "
                       f"(parent {parent[:12]})")  # fmt: skip
            before, after = originals.get(parent), codes.get(sid)
            if before is None or after is None:
                out.append("    (parent or marked code not found)")
                continue
            out += [f"    {line}" for line in diff_lines(str(before), str(after))]
        return out

    def suffix_block(self, d: dict[str, Any], lang: str) -> list[str]:
        marked, base = d["marked"], d["baseline"]
        originals = dict(zip(base["sample_id"].astype(str), base["code"], strict=True))
        pairs = [
            (str(code), str(originals[str(p)]))
            for code, p in zip(marked["code"], marked["parent_id"], strict=True)
            if isinstance(code, str) and isinstance(originals.get(str(p)), str)
        ]
        s = suffix_stats(pairs, lang)
        last = ", ".join(f"{k} {v}" for k, v in s["last"])
        return [
            f"  (c) new names {s['n']} (changed samples): EXTRA_SUFFIXES {rid._v(s['extra'])}, "
            f"digit {rid._v(s['digit'])}, either {rid._v(s['extra_or_digit'])}; base suffixes "
            f"{rid._v(s['base'])}, prefixes {rid._v(s['prefix'])}",
            f"      most frequent last segments: {last or '-'}",
        ]

    def common_lines(self) -> list[str]:
        out = [f"## features in the top {self.top} of at least {COMMON_IN} cells"]
        for name, sets in self.tops.items():
            counts: Counter[tuple[str, str]] = Counter(f for s in sets for f in s)
            common = sorted((f for f, n in counts.items() if n >= COMMON_IN),
                            key=lambda f: (-counts[f], f))  # fmt: skip
            out.append(f"  {name}: " + (", ".join(f"{k}:{f!r} ({counts[(k, f)]})"
                                                   for k, f in common) or "none"))  # fmt: skip
        return [*out, ""]


def _commit() -> str:
    """Commit di wm-bench (``unknown`` fuori da un repository Git)."""
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True, timeout=30)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument("--suffix-configs", nargs="+", default=["B", "C", "D"],
                   help="labels for the suffix analysis (c)")  # fmt: skip
    p.add_argument("--top", type=int, default=DEFAULT_TOP)
    p.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.split == "test":
        print("[ERROR] development diagnosis only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=adversarial_logreg", *args.overrides])
    diag = Diagnosis(cfg, args.top, args.samples)
    lines = [
        "# adversarial_logreg diagnosis on development (scripts/diag_imperceptibility_dev.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# wm-bench {_commit()}; configs: {', '.join(c.name for c in args.config)}; split "
        f"{args.split}, language {args.language}; read-only, nothing written to the store",
        "# (a) model on the whole cell, C from the inner CV of the stage run on the whole cell; "
        "weight = coefficient towards 'marked' (explicit features scaled by MaxAbsScaler); "
        "marked/baseline = mean value (fraction for name/node, TF-IDF for ngram)",
        "# (b) out-of-fold scores of the stage; diff baseline -> marked (parent_id)",
        "# (c) new names = identifiers of the marked code missing from the parent, no builtins",
        "",
    ]
    for model in args.models or list(cfg.models):
        for level in args.levels:
            for c in args.config:
                lines.append(f"## {c.name}  {model} {level} {args.language} {args.split}")
                try:
                    d = diag.data(c, model, level, args.language, args.split)
                except Exception as exc:  # artefatto mancante: si segnala
                    lines += [f"  missing artifacts ({type(exc).__name__}: {exc})", ""]
                    continue
                lines += diag.features_block(c, d, model, args.language)
                lines += diag.samples_block(d)
                if c.method == "multimark" and c.label in args.suffix_configs:
                    lines += diag.suffix_block(d, args.language)
                lines.append("")
    lines += diag.common_lines()
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
