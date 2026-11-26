"""Esempi di codice per la tesi dal test di P6 di ``mcss`` e ``multimark``, in sola lettura.

Uso (con bench-core; srun su CPU, nessuna GPU):
    python scripts/report_attack_examples.py \\
        --md docs/milestone_logs/P6_examples_test.md --tex-dir $WMB/report_p6/examples

Nessun nuovo calcolo: legge dallo store gli artefatti del test (baseline, marcati, attaccati,
rilevazione, esecuzione) e le soglie congelate (controllate contro lo sha256 del congelamento,
come la fase metrics); non scrive niente nello store. Le due uscite non devono esistere: nessun
file si sovrascrive.

Le tre versioni sono le configurazioni congelate ``mcss`` main, ``multimark`` prudent e
``multimark`` high_capacity (linguaggio python, i due modelli).

- **Marchio**: 3 esempi di L1 test e 2 di L2 test. Per ogni versione: codice originale della
  baseline (non marcato), codice marcato, diff, stato dell'inserimento, siti (``n_sites``),
  punteggio dell'originale (``neg_llm``) e del marcato (``positives``) con la soglia congelata e
  test passati.
- **Attacchi**: un punto per attacco (``ATTACKS``), 3 esempi ciascuno. Per ogni versione: codice
  marcato, codice attaccato, diff, stato dell'attacco, punteggio prima e dopo con la soglia
  congelata (rilevato sì/no), test passati prima e dopo, 1-CodeBLEU; per T3.1 anche il punteggio
  dopo con k2 e la soglia di k2 congelata.

**Scelta degli esempi** (fissata prima di guardarli, stampata in testa al Markdown): un esempio è
una terna (modello, problema, indice del campione) presente in tutte e tre le versioni; si tengono
solo le terne con codice marcato diverso dall'originale (marchio: inserimento ``OK``/``PARTIAL``)
o con stato ``APPLIED`` (attacchi) in **tutte e tre** le versioni. Le terne si ordinano per numero
di righe del codice originale della baseline (comune alle tre versioni; a parità, per terna) e si
dividono in tre terzi (corto, medio, lungo); in ogni terzo si sceglie la terna con il valore più
basso di ``derive_seed(SEED, etichetta, modello, problema, indice)``. Con due esempi (marchio L2):
corto e lungo.

**Rilevato**: ``bench.metrics.calibration.detected`` (punteggio >= soglia, rilevazione riuscita,
inserimento non ``FAILED``), con la soglia ``thresholds/.../L1L2/<config>.json`` della cella.

**LaTeX**: un ``.tex`` per esempio con ambienti ``lstlisting`` (codice marcato, attaccato e diff)
e una tabella delle misure, più ``preamble.tex`` con lo stile ``wmb`` e il linguaggio ``wmbdiff``
da includere nel preambolo della tesi (``\\input{preamble.tex}``).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from bench.attacks.base import grid_attacks
from bench.config.builder import load_experiment
from bench.domain.errors import BenchError
from bench.domain.ids import derive_seed
from bench.domain.models import ThresholdSet
from bench.hpo.freeze import FINAL_LEVELS, data_sha256, load_freeze
from bench.metrics.calibration import detected
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.calibrate import threshold_ref
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import baseline_ref
from bench.pipeline.stages.watermark import watermarked_ref
from bench.store.artifact_store import ArtifactStore

SEED = 20261126
LANGUAGE, SPLIT = "python", "test"
VERSIONS = (("mcss", "main"), ("multimark", "prudent"), ("multimark", "high_capacity"))
SIZES = ("corto", "medio", "lungo")
MARK_LEVELS = (("L1", 3), ("L2", 2))
# (etichetta, attacco, filtro sui parametri del punto, livello)
ATTACKS: tuple[tuple[str, str, dict[str, Any], str], ...] = (
    ("T1.1", "T1.1", {}, "L1"),
    ("T1.2", "T1.2", {}, "L1"),
    ("T1.3 remove", "T1.3", {"mode": "remove"}, "L1"),
    ("T1.3 insert", "T1.3", {"mode": "insert"}, "L1"),
    ("T1.4 natural r=1.0", "T1.4", {"name_kind": "natural", "ratio": 1.0}, "L1"),
    ("T1.4 random r=0.5", "T1.4", {"name_kind": "random", "ratio": 0.5}, "L1"),
    ("T1.5 k=5", "T1.5", {"k": 5}, "L1"),
    ("T1.6 convert", "T1.6", {"caps": "convert"}, "L1"),
    ("T2.1 b=all", "T2.1", {"b": "all"}, "L1"),
    ("T2.2 conservative", "T2.2", {"variant": "conservative"}, "L1"),
    ("T2.2 full_rewrite", "T2.2", {"variant": "full_rewrite"}, "L1"),
    ("T2.4 p=0.5", "T2.4", {"p": 0.5}, "L1"),
    ("T2.4k k=2", "T2.4k", {"k": 2}, "L2"),
    ("T2.5 n=2 around", "T2.5", {"n": 2, "position": "around"}, "L1"),
    ("T3.1", "T3.1", {}, "L1"),
    ("T4.1 rename", "T4.1", {"rename": True}, "L1"),
)
# Parametri che distinguono i punti (gli altri sono ``fixed`` delle griglie: regole, prompt, ...).
POINT_KEYS = ("mode", "density", "ratio", "name_kind", "k", "caps", "b", "variant", "p", "n",
              "position", "key_id", "rename")  # fmt: skip
MARK_HEAD = ("versione", "inserimento", "siti", "punteggio originale", "punteggio marcato",
             "test originale", "test marcato")  # fmt: skip
RULE = (
    "terne (modello, problema, indice) presenti nelle tre versioni, con codice marcato diverso "
    "dall'originale (marchio) o stato APPLIED (attacchi) in tutte e tre; ordinate per righe del "
    "codice originale della baseline e divise in tre terzi (corto, medio, lungo; con due esempi "
    "corto e lungo); in ogni terzo la terna con derive_seed(SEED, etichetta, modello, problema, "
    "indice) minimo"
)

Key = tuple[str, str, int]  # (modello, problema, indice del campione)


# --------------------------------------------------------------------------- scelta
def choose(lengths: dict[Key, int], label: str, n: int) -> list[tuple[str, Key]]:
    """Esempi (taglia, terna) secondo ``RULE``: terzi per righe, poi seme minimo nel terzo."""
    ranked = sorted(lengths, key=lambda k: (lengths[k], k))
    if not ranked:
        return []
    parts = len(SIZES)
    thirds = [
        ranked[i * len(ranked) // parts : (i + 1) * len(ranked) // parts] for i in range(parts)
    ]
    wanted = tuple(range(parts)) if n == parts else (0, parts - 1)
    out: list[tuple[str, Key]] = []
    for i in wanted:
        group = thirds[i] or ranked  # meno di tre candidati: dal totale
        best = min(group, key=lambda k: (derive_seed(SEED, label, *k), k))
        if best not in [k for _, k in out]:
            out.append((SIZES[i], best))
    return out


def n_lines(code: str) -> int:
    return len(code.splitlines())


def unified(before: str, after: str, a: str, b: str) -> str:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), a, b, lineterm="")
    return "\n".join(lines)


# --------------------------------------------------------------------------- dati
@dataclass
class Version:
    """Tabelle di una versione (metodo, etichetta) su un livello, per modello."""

    method: str
    label: str
    level: str
    hashes: dict[str, str]  # modello -> config_hash
    tau: dict[str, float]  # modello -> soglia congelata (k1)
    baseline: dict[Key, dict[str, Any]] = field(default_factory=dict)
    marked: dict[Key, dict[str, Any]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"{self.method} {self.label}"


class Reader:
    """Letture dallo store (nessuna scrittura), con le soglie congelate controllate."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store
        self.problems: list[str] = []
        self._cache: dict[str, pd.DataFrame | None] = {}

    def table(self, ref: Any) -> pd.DataFrame | None:
        """Tabella dello store, ``None`` (e un problema annotato) se manca. Le tabelle degli
        attaccati si leggono una volta sola e non restano in memoria."""
        path = str(ref.path)
        if path in self._cache:
            return self._cache[path]
        try:
            table: pd.DataFrame | None = self.store.read_table(ref)
        except BenchError as exc:
            self.problems.append(f"missing {path}: {type(exc).__name__}")
            table = None
        if "attacked" not in path:
            self._cache[path] = table
        return table

    def rows(self, ref: Any) -> list[dict[str, Any]]:
        table = self.table(ref)
        if table is None:
            return []
        return [{str(k): v for k, v in r.items()} for r in table.to_dict(orient="records")]

    def by_id(self, ref: Any) -> dict[str, dict[str, Any]]:
        return {str(r["sample_id"]): r for r in self.rows(ref)}

    def threshold(self, method: str, model: str, cfg_hash: str, key_id: str = "k1") -> float:
        """Soglia congelata (I3): sha256 uguale a quello del congelamento, altrimenti errore."""
        record = load_freeze(self.store, method, key_id)
        frozen = record.cell(model, LANGUAGE, cfg_hash) if record is not None else None
        ref = threshold_ref(method, model, LANGUAGE, cfg_hash, FINAL_LEVELS, key_id)
        if frozen is None or data_sha256(self.store, ref) != frozen.threshold_sha256:
            raise SystemExit(f"##### {method} {model} {cfg_hash} {key_id}: {ref.path} not frozen")
        return float(self.store.read_model(ref, ThresholdSet).threshold)

    def version(self, method: str, label: str, level: str) -> Version:
        record = load_freeze(self.store, method)
        if record is None:
            raise SystemExit(f"##### {method}: NOT FROZEN")
        cells = [c for c in record.cells if c.language == LANGUAGE and c.label == label]
        hashes = {c.model_id: str(c.config_hash) for c in cells if c.config_hash}
        if not hashes:
            raise SystemExit(f"##### {method} {label}: no frozen python cell")
        v = Version(method, label, level, hashes,
                    {m: self.threshold(method, m, h) for m, h in hashes.items()})  # fmt: skip
        for model, h in hashes.items():
            base = self.by_id(baseline_ref(model, level, LANGUAGE, SPLIT))
            neg = self.by_id(detection_ref(method, model, h, "neg_llm", level, LANGUAGE, SPLIT))
            pos = self.by_id(detection_ref(method, model, h, "positives", level, LANGUAGE, SPLIT))
            cell = Cell(method=method, model_id=model, level=level, language=LANGUAGE, split=SPLIT)
            ex_base = self.by_id(execution_ref(_with(cell, source="llm_baseline")))
            ex_mark = self.by_id(execution_ref(_with(cell, source="llm_watermarked"), h))
            for sid, row in base.items():
                key = (model, str(row["problem_key"]), int(row["sample_index"] or 0))
                v.baseline[key] = {**row, "det": neg.get(sid), "exec": ex_base.get(sid)}
            for row in self.rows(watermarked_ref(method, model, h, level, LANGUAGE, SPLIT)):
                key = (model, str(row["problem_key"]), int(row["sample_index"] or 0))
                sid = str(row["sample_id"])
                v.marked[key] = {**row, "det": pos.get(sid), "exec": ex_mark.get(sid)}
        return v

    def attacked(self, v: Version, attack_id: str, params_hash: str) -> dict[Key, dict[str, Any]]:
        """Righe attaccate per terna, con rilevazione (k1; k2 per T3.1) ed esecuzione."""
        out: dict[Key, dict[str, Any]] = {}
        subject = attacked_subject(attack_id, params_hash)
        for model, h in v.hashes.items():
            det = self.by_id(detection_ref(v.method, model, h, subject, v.level, LANGUAGE, SPLIT))
            det_k2 = (
                self.by_id(
                    detection_ref(v.method, model, h, subject, v.level, LANGUAGE, SPLIT, "k2")
                )
                if attack_id == "T3.1"
                else {}
            )
            cell = Cell(
                method=v.method, model_id=model, level=v.level, language=LANGUAGE, split=SPLIT,
                attack_id=attack_id, attack_params_hash=params_hash, source="attacked",
            )  # fmt: skip
            ex = self.by_id(execution_ref(cell, h))
            ref = attacked_ref(attack_id, params_hash, v.method, model, h, v.level, LANGUAGE, SPLIT)
            for row in self.rows(ref):
                key = (model, str(row["problem_key"]), int(row["sample_index"] or 0))
                sid = str(row["sample_id"])
                out[key] = {**row, "det": det.get(sid), "det_k2": det_k2.get(sid),
                            "exec": ex.get(sid)}  # fmt: skip
        return out


def _with(cell: Cell, **changes: Any) -> Cell:
    from dataclasses import replace

    return replace(cell, **changes)


def point(attack_id: str, wanted: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(params_hash, parametri) dell'unico punto della griglia che rispetta il filtro."""
    found = [
        (h, a.params)
        for h, a in grid_attacks(attack_id).items()
        if all(a.params.get(k) == val for k, val in wanted.items())
    ]
    if len(found) != 1:
        raise SystemExit(f"##### {attack_id} {wanted}: {len(found)} grid points")
    return found[0]


# --------------------------------------------------------------------------- misure
def _num(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def decision(det: dict[str, Any] | None, tau: float) -> tuple[float | None, bool]:
    """Punteggio e decisione (``detected``) di una riga di rilevazione."""
    if det is None:
        return None, False
    score = _num(det.get("score"))
    embed = det.get("embed_status")
    embed_s = None if embed is None or (isinstance(embed, float) and pd.isna(embed)) else str(embed)
    return score, detected(score, str(det.get("status")), embed_s, tau)


def tests(ex: dict[str, Any] | None) -> str:
    if ex is None:
        return "—"
    passed, total = _num(ex.get("n_passed")), _num(ex.get("n_tests"))
    count = "" if total is None else f" {int(passed or 0)}/{int(total)}"
    return f"{ex.get('status')}{count}"


def score_text(score: float | None, hit: bool) -> str:
    return "—" if score is None else f"{score:.3f} ({'rilevato' if hit else 'non rilevato'})"


# --------------------------------------------------------------------------- Markdown
def fence(code: str, lang: str) -> str:
    mark = "~~~~" if "```" in code else "```"
    return f"{mark}{lang}\n{code.rstrip()}\n{mark}\n"


def md_table(rows: Sequence[tuple[str, ...]]) -> str:
    head, *body = rows
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- LaTeX
TEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
               "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
               "^": r"\textasciicircum{}"}  # fmt: skip
PREAMBLE = r"""% Stile di scripts/report_attack_examples.py (pacchetti listings, xcolor, graphicx).
% Con codice non ASCII compilare con XeLaTeX o LuaLaTeX.
\usepackage{listings}
\usepackage{xcolor}
\usepackage{graphicx}
\lstdefinestyle{wmb}{basicstyle=\ttfamily\scriptsize, columns=fullflexible, keepspaces=true,
  breaklines=true, frame=single, numbers=left, numberstyle=\tiny, showstringspaces=false,
  extendedchars=true, upquote=true}
\lstdefinelanguage{wmbdiff}{morecomment=[f][\color{red!70!black}]-,
  morecomment=[f][\color{green!45!black}]+, morecomment=[f][\color{blue!60!black}]{@@},
  morecomment=[f][\color{gray}]{---}, morecomment=[f][\color{gray}]{+++}}
"""


def tex_escape(text: str) -> str:
    return "".join(TEX_SPECIAL.get(ch, ch) for ch in text)


def listing(code: str, language: str, caption: str) -> str:
    if r"\end{lstlisting}" in code:
        raise ValueError("code contains \\end{lstlisting}")
    options = f"style=wmb, language={language}, caption={{{tex_escape(caption)}}}"
    return f"\\begin{{lstlisting}}[{options}]\n{code.rstrip()}\n\\end{{lstlisting}}\n"


def tex_table(rows: Sequence[tuple[str, ...]]) -> str:
    head, *body = rows
    cols = "l" * len(head)
    lines = [" & ".join(tex_escape(c) for c in r) + r" \\" for r in body]
    head_line = " & ".join(rf"\textbf{{{tex_escape(c)}}}" for c in head) + r" \\ \hline"
    return (
        f"\\noindent\\resizebox{{\\linewidth}}{{!}}{{%\n\\begin{{tabular}}{{{cols}}}\n{head_line}\n"
        + "\n".join(lines)
        + "\n\\end{tabular}}\n"
    )


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


# --------------------------------------------------------------------------- sezioni
@dataclass
class Example:
    """Un esempio: testo Markdown e file LaTeX."""

    md: str
    tex_name: str
    tex: str


def mark_examples(versions: list[Version], level: str, n: int) -> tuple[str, list[Example]]:
    keys = set.intersection(*(set(v.marked) for v in versions))
    ok = {
        k: n_lines(str(versions[0].baseline[k]["code"]))
        for k in keys
        if k in versions[0].baseline
        and all(
            str(v.marked[k]["embed_status"]) in ("OK", "PARTIAL")
            and v.marked[k]["code"] != v.baseline[k]["code"]
            for v in versions
        )
    }
    label = f"mark {level}"
    head = f"Candidati: {len(ok)} terne su {len(keys)} comuni alle tre versioni.\n"
    out = []
    for size, key in choose(ok, label, n):
        model, problem, index = key
        original = str(versions[0].baseline[key]["code"])
        title = f"{level} {size}: {model} {problem} #{index} ({n_lines(original)} righe)"
        rows: list[tuple[str, ...]] = [MARK_HEAD]
        md = [f"### {title}\n", "Codice originale (baseline, non marcato):\n",
              fence(original, "python")]  # fmt: skip
        tex = [f"% {title}\n", listing(original, "Python", f"{title} - originale")]
        for v in versions:
            mark, base = v.marked[key], v.baseline[key]
            tau = v.tau[model]
            s0, d0 = decision(base["det"], tau)
            s1, d1 = decision(mark["det"], tau)
            sites = _num(mark.get("n_sites"))
            n_sites = "—" if sites is None else str(int(sites))
            rows.append((v.name, str(mark["embed_status"]), n_sites, score_text(s0, d0),
                         score_text(s1, d1), tests(base["exec"]), tests(mark["exec"])))  # fmt: skip
            diff = unified(original, str(mark["code"]), "originale", v.name)
            md += [f"**{v.name}** (soglia {tau:.4f}): diff originale → marcato\n",
                   fence(diff, "diff")]  # fmt: skip
            tex.append(listing(diff, "wmbdiff", f"{title} - {v.name}: diff originale/marcato"))
        md.insert(1, md_table(rows))
        tex.append(tex_table(rows))
        out.append(Example("\n".join(md), f"mark_{level}_{size}.tex", "\n".join(tex)))
    return head, out


def attack_examples(
    versions: list[Version], reader: Reader, label: str, attack_id: str, params_hash: str
) -> tuple[str, list[Example]]:
    rows_by_v = [reader.attacked(v, attack_id, params_hash) for v in versions]
    keys = set.intersection(*(set(r) for r in rows_by_v)) if rows_by_v else set()
    base = versions[0].baseline
    ok = {
        k: n_lines(str(base[k]["code"]))
        for k in keys
        if k in base and all(str(r[k]["attack_status"]) == "APPLIED" for r in rows_by_v)
    }
    head = f"Candidati: {len(ok)} terne APPLIED su {len(keys)} comuni alle tre versioni.\n"
    k2 = attack_id == "T3.1"
    out = []
    for size, key in choose(ok, label, 3):
        model, problem, index = key
        title = (
            f"{label} {size}: {model} {problem} #{index} ({n_lines(str(base[key]['code']))} righe)"
        )
        head_row = ["versione", "stato", "punteggio prima", "punteggio dopo"]
        head_row += ["punteggio dopo (k2)"] if k2 else []
        head_row += ["test prima", "test dopo", "1-CodeBLEU"]
        rows: list[tuple[str, ...]] = [tuple(head_row)]
        md = [f"### {title}\n"]
        tex = [f"% {title}\n"]
        for v, attacked in zip(versions, rows_by_v, strict=True):
            mark, att = v.marked[key], attacked[key]
            tau = v.tau[model]
            s0, d0 = decision(mark["det"], tau)
            s1, d1 = decision(att["det"], tau)
            row = [v.name, str(att["attack_status"]), score_text(s0, d0), score_text(s1, d1)]
            if k2:
                tau_k2 = reader.threshold(v.method, model, v.hashes[model], "k2")
                s2, d2 = decision(att["det_k2"], tau_k2)
                row.append(score_text(s2, d2))
            dist = _num(att.get("codebleu_distance"))
            row += [tests(mark["exec"]), tests(att["exec"]),
                    "—" if dist is None else f"{dist:.3f}"]  # fmt: skip
            rows.append(tuple(row))
            marked_code, attacked_code = str(mark["code"]), str(att["code"])
            diff = unified(marked_code, attacked_code, "marcato", "attaccato")
            md += [f"**{v.name}** (soglia {tau:.4f})\n", "Marcato:\n", fence(marked_code, "python"),
                   "Attaccato:\n", fence(attacked_code, "python"), "Diff marcato → attaccato:\n",
                   fence(diff, "diff")]  # fmt: skip
            tex += [listing(marked_code, "Python", f"{title} - {v.name}: marcato"),
                    listing(attacked_code, "Python", f"{title} - {v.name}: attaccato"),
                    listing(diff, "wmbdiff", f"{title} - {v.name}: diff")]  # fmt: skip
        md.insert(1, md_table(rows))
        tex.insert(1, tex_table(rows))
        out.append(Example("\n".join(md), f"attack_{slug(label)}_{size}.tex", "\n".join(tex)))
    return head, out


# --------------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--md", required=True, help="Markdown output (must not exist)")
    parser.add_argument("--tex-dir", required=True, help="LaTeX folder (must not exist)")
    args = parser.parse_args()
    md_path, tex_dir = Path(args.md), Path(args.tex_dir)
    for path in (md_path, tex_dir):
        if path.exists():
            sys.stdout.write(f"##### {path} exists: never overwritten\n")
            return 1
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    reader = Reader(ArtifactStore(Path(cfg.paths.artifacts)))
    by_level = {
        level: [reader.version(m, label, level) for m, label in VERSIONS]
        for level in sorted({lv for lv, _ in MARK_LEVELS} | {a[3] for a in ATTACKS})
    }
    sections: list[str] = []
    examples: list[Example] = []
    sections.append("## Marchio\n")
    for level, n in MARK_LEVELS:
        head, found = mark_examples(by_level[level], level, n)
        sections += [f"#### {level} test\n", head, *(e.md for e in found)]
        examples += found
    sections.append("## Attacchi\n")
    for label, attack_id, wanted, level in ATTACKS:
        params_hash, params = point(attack_id, wanted)
        head, found = attack_examples(by_level[level], reader, label, attack_id, params_hash)
        shown = {k: v for k, v in params.items() if k in POINT_KEYS}
        sections += [f"## {label} ({level}, {params_hash})\n", f"Parametri del punto: `{shown}`\n",
                     head, *(e.md for e in found)]  # fmt: skip
        if len(found) < len(SIZES):
            reader.problems.append(f"{label}: {len(found)} examples")
        examples += found
    thresholds = "; ".join(
        f"{v.name} " + ", ".join(f"{m} {t:.4f}" for m, t in v.tau.items()) for v in by_level["L1"]
    )
    header = [
        "# P6 — esempi di codice dal test (mcss, multimark)\n",
        f"Generato il {datetime.now(UTC).isoformat(timespec='seconds')} da "
        "`scripts/report_attack_examples.py`, in sola lettura (nessun nuovo calcolo).\n",
        f"- **Seme:** `SEED = {SEED}`\n- **Regola:** {RULE}.\n"
        "- **Rilevato:** punteggio >= soglia congelata (L1+L2, sha256 del congelamento "
        f"controllato), rilevazione riuscita, inserimento non FAILED.\n"
        f"- **Soglie (k1):** {thresholds}.\n"
        f"- **Problemi di lettura:** {'; '.join(reader.problems) or 'nessuno'}.\n",
    ]
    tex_dir.mkdir(parents=True)
    (tex_dir / "preamble.tex").write_text(PREAMBLE, encoding="utf-8")
    for e in examples:
        with open(tex_dir / e.tex_name, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(e.tex)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(md_path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join([*header, *sections]))
    sys.stdout.write(
        f"##### {len(examples)} examples: {md_path}, {tex_dir} ({len(reader.problems)} problems)\n"
    )
    for problem in reader.problems:
        sys.stdout.write(f"  {problem}\n")
    return 0 if not reader.problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
