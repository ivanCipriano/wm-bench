"""Project CodeNet per il Livello 2: metadati, idoneità dei problemi e testo dei problemi (M9).

Regole (cluster_info §8.6, SPEC §10.4; decisioni dell'utente del 7 e del 13 ottobre 2026):

- un problema è idoneo se ha il caso di I/O, non è in ``no_solutions.txt``, in
  ``unverified_accepted_solutions.txt`` (elenca problemi) né fra gli esclusi dell'oracle, ha una
  descrizione in inglese senza immagini e, in **ciascuno** dei 4 linguaggi, almeno 6 sottomissioni
  ``Accepted`` di utenti distinti, in una versione compatibile con la sandbox (Python 3, C++
  compilabile come C++17, Java, JavaScript su Node.js);
- le sottomissioni di un linguaggio si ordinano per
  ``derive_seed(global_seed, "codenet-sub", pid, linguaggio, submission_id)``: la prima è la
  soluzione canonica, le 5 successive sono i negativi umani; si salta una sottomissione di un utente
  già preso o quasi duplicata (``derived/duplicates``) di una già presa;
- i problemi si ordinano per ``derive_seed(global_seed, "codenet-select", pid)``; dello stesso
  cluster di ``identical_problem_clusters`` resta al più uno (il primo idoneo nell'ordine).

Il conteggio dei problemi rimasti dopo ogni criterio va nel manifest della selezione.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from bench.domain.enums import Language
from bench.domain.errors import DataError
from bench.domain.ids import derive_seed
from bench.execution.io_check import normalize_output  # noqa: F401 - stessa regola della sandbox

PID = re.compile(r"p\d{5}")
SID = re.compile(r"s\d{9}")
# Valore della colonna ``language`` dei metadati e nome della cartella in ``data/<pid>/``.
LANG_NAMES: dict[Language, str] = {
    Language.PYTHON: "Python",
    Language.JAVA: "Java",
    Language.CPP: "C++",
    Language.JAVASCRIPT: "JavaScript",
}
# Cartella di ``derived/duplicates`` per linguaggio (JavaScript non c'è: impronta del codice).
DUPLICATE_DIRS: dict[Language, str] = {
    Language.PYTHON: "Python",
    Language.JAVA: "Java",
    Language.CPP: "C++",
}
DEFAULT_EXT: dict[Language, str] = {
    Language.PYTHON: "py",
    Language.JAVA: "java",
    Language.CPP: "cpp",
    Language.JAVASCRIPT: "js",
}
N_NEGATIVES = 5
N_PER_LANGUAGE = 1 + N_NEGATIVES  # canonica + negativi
N_EXTRACT = 10  # sottomissioni estratte per linguaggio (riserva per la deduplicazione del codice)
_JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


# --------------------------------------------------------------------------- versioni compatibili
def compatible(lang: Language, original_language: str) -> bool:
    """La versione della sottomissione gira nella sandbox (Python 3.11, g++ C++17, JDK 17, Node)?

    - Python: solo Python 3 o PyPy3 (niente Python 2, né "Python" senza versione);
    - C++: GCC o Clang (niente MSVC, niente C++20);
    - Java: tutte le versioni (la classe ``Main`` si verifica con la validazione);
    - JavaScript: solo Node.js (anche "JavaScript" senza motore, che su AIZU è Node).
    """
    text = original_language.strip().lower()
    if lang is Language.PYTHON:
        return re.search(r"(python|pypy)\s*\(?\s*3", text) is not None
    if lang is Language.CPP:
        return not re.search(r"msvc|visual|c\+\+\s*20|c\+\+2a", text)
    if lang is Language.JAVA:
        return True
    if lang is Language.JAVASCRIPT:
        return "node" in text or text == "javascript"
    return False


# --------------------------------------------------------------------------- file derivati
def parse_groups(text: str, pattern: re.Pattern[str]) -> list[frozenset[str]]:
    """Gruppi di identificativi (cluster di problemi o di sottomissioni quasi duplicate).

    Se il file separa i gruppi con righe vuote, un gruppo è un blocco; altrimenti è una riga.
    Si tengono solo i gruppi con almeno due identificativi.
    """
    text = text.replace("\r\n", "\n").strip()
    chunks = re.split(r"\n[ \t]*\n", text) if re.search(r"\n[ \t]*\n", text) else text.split("\n")
    groups = [frozenset(pattern.findall(chunk)) for chunk in chunks]
    return [g for g in groups if len(g) > 1]


def group_index(groups: Iterable[frozenset[str]]) -> dict[str, int]:
    """Identificativo → indice del suo gruppo."""
    return {item: i for i, group in enumerate(groups) for item in group}


# --------------------------------------------------------------------------- descrizioni
@dataclass
class Description:
    """Testo di un problema convertito da HTML, con le caratteristiche usate dai criteri."""

    text: str
    n_images: int
    has_japanese: bool
    has_math: bool
    has_table: bool


_BLOCK = frozenset(
    {
        "p",
        "div",
        "section",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "table",
        "center",
        "blockquote",
        "dl",
        "dt",
        "dd",
        "hr",
    }
)
_SKIP = frozenset({"script", "style", "head", "title"})


class _TextParser(HTMLParser):
    """Conversione HTML → testo (vedi ``describe``)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.pre = 0
        self.n_images = 0
        self.n_tables = 0
        self.cell = 0

    def _emit(self, text: str) -> None:
        self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif tag == "pre":
            self.pre += 1
            self._emit("\n\x00PRE\n")
        elif tag == "br":
            self._emit("\n")
        elif tag == "li":
            self._emit("\n- ")
        elif tag == "tr":
            self.cell = 0
            self._emit("\n")
        elif tag in ("td", "th"):
            if self.cell:
                self._emit(" | ")
            self.cell += 1
        elif tag == "var" and not self.pre:  # nei <pre> i formati restano testo semplice
            self._emit("$")
        elif tag == "img":
            self.n_images += 1
            self._emit("[image]")
        elif tag in _BLOCK:
            self.n_tables += tag == "table"
            self._emit("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self._emit("\n\x00/PRE\n")
        elif tag == "var" and not self.pre:
            self._emit("$")
        elif tag in _BLOCK:
            self._emit("\n\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self._emit(data)
        else:
            self._emit(re.sub(r"\s+", " ", data))


def _english_part(html: str) -> str:
    """Per i testi AtCoder bilingui, solo il contenuto di ``<span class="lang-en">``."""
    start = re.search(r"<span\s+class=[\"']lang-en[\"']\s*>", html, re.IGNORECASE)
    if start is None:
        return html
    # Fine dello span: si contano gli span annidati.
    depth, pos = 1, start.end()
    for tag in re.finditer(r"<(/?)span\b[^>]*>", html[pos:], re.IGNORECASE):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[pos : pos + tag.start()]
    return html[pos:]


def _layout(raw: str) -> str:
    """Spazi e righe vuote fuori dai blocchi ``<pre>``, che restano identici."""
    out: list[str] = []
    for i, chunk in enumerate(re.split(r"\n\x00/?PRE\n", raw)):
        if i % 2:  # dentro <pre>
            out.append("```\n" + chunk.strip("\n") + "\n```")
            continue
        lines = [line.strip() for line in chunk.split("\n")]
        out.append(re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n"))
    text = "\n\n".join(part for part in out if part.strip())
    return re.sub(r"\$\$", "", text).strip() + "\n"  # <var> vuoti o adiacenti


def describe(html: str) -> Description:
    """Testo del problema.

    - Testi AtCoder bilingui: solo la parte inglese (``span.lang-en``).
    - Blocchi (paragrafi, titoli, liste) su righe proprie; ``<li>`` → ``- ``.
    - ``<pre>`` (esempi di input/output) in un blocco recintato, con il contenuto invariato.
    - ``<var>`` → ``$...$`` fuori dai ``<pre>``; dentro i ``<pre>`` (formati di input e output)
      solo il contenuto, con spazi e a capo invariati; il TeX già presente resta com'è.
    - Tabelle: una riga per ``<tr>``, celle separate da `` | ``.
    - Immagini: ``[image]`` (e conteggio, criterio di esclusione).
    - Si toglie il BOM (U+FEFF); gli altri caratteri restano invariati.
    """
    parser = _TextParser()
    parser.feed(_english_part(html))
    parser.close()
    text = _layout(unescape("".join(parser.parts)).replace("﻿", ""))
    return Description(
        text=text,
        n_images=parser.n_images,
        has_japanese=_JAPANESE.search(text) is not None,
        has_math=bool(re.search(r"\$[^$\n]+\$|\\\(|\\\[", text)),
        has_table=parser.n_tables > 0,
    )


# --------------------------------------------------------------------------- selezione
@dataclass(frozen=True)
class Submission:
    """Sottomissione di CodeNet."""

    submission_id: str
    user_id: str
    language: Language
    original_language: str
    ext: str

    def relpath(self, pid: str) -> str:
        """Percorso relativo a ``Project_CodeNet/data``."""
        return f"{pid}/{LANG_NAMES[self.language]}/{self.submission_id}.{self.ext}"

    def as_dict(self) -> dict[str, str]:
        return {
            "submission_id": self.submission_id,
            "user_id": self.user_id,
            "original_language": self.original_language,
            "ext": self.ext,
        }


def rank_problem(seed: int, pid: str) -> int:
    """Rango del problema nella selezione (minore = prima)."""
    return int(derive_seed(seed, "codenet-select", pid))


def pick_submissions(
    seed: int,
    pid: str,
    lang: Language,
    rows: Sequence[Mapping[str, str]],
    unverified: set[str],
    duplicates: Mapping[str, int],
    limit: int = N_EXTRACT,
) -> list[Submission]:
    """Sottomissioni accettate, verificate e compatibili, in ordine di ``derive_seed``.

    Si salta una sottomissione di un utente già preso o nello stesso gruppo di quasi duplicati
    di una già presa. Al più ``limit``.
    """
    name = LANG_NAMES[lang]
    pool = [
        r
        for r in rows
        if r.get("language") == name
        and r.get("status") == "Accepted"
        and r["submission_id"] not in unverified
        and compatible(lang, r.get("original_language", ""))
    ]
    pool.sort(
        key=lambda r: (
            derive_seed(seed, "codenet-sub", pid, str(lang), r["submission_id"]),
            r["submission_id"],
        )
    )
    chosen: list[Submission] = []
    users: set[str] = set()
    groups: set[int] = set()
    for r in pool:
        group = duplicates.get(r["submission_id"])
        if r["user_id"] in users or (group is not None and group in groups):
            continue
        users.add(r["user_id"])
        if group is not None:
            groups.add(group)
        chosen.append(
            Submission(
                submission_id=r["submission_id"],
                user_id=r["user_id"],
                language=lang,
                original_language=r.get("original_language", ""),
                ext=r.get("filename_ext") or DEFAULT_EXT[lang],
            )
        )
        if len(chosen) == limit:
            break
    return chosen


@dataclass
class Candidate:
    """Problema che supera i criteri sui metadati e sul testo."""

    pid: str
    rank: int
    source: str  # AtCoder o AIZU (problem_list.csv)
    submissions: dict[Language, list[Submission]]
    has_math: bool
    has_table: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "rank": self.rank,
            "source": self.source,
            "has_math": self.has_math,
            "has_table": self.has_table,
            "submissions": {
                str(lang): [s.as_dict() for s in subs] for lang, subs in self.submissions.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Candidate:
        return cls(
            pid=str(data["pid"]),
            rank=int(data["rank"]),
            source=str(data.get("source", "")),
            has_math=bool(data.get("has_math")),
            has_table=bool(data.get("has_table")),
            submissions={
                Language(lang): [Submission(language=Language(lang), **s) for s in subs]
                for lang, subs in data["submissions"].items()
            },
        )


@dataclass
class Funnel:
    """Problemi rimasti dopo ogni criterio, nell'ordine di applicazione."""

    steps: list[tuple[str, int]] = field(default_factory=list)

    def add(self, name: str, n: int) -> None:
        self.steps.append((name, n))

    def as_list(self) -> list[dict[str, Any]]:
        return [{"criterion": name, "remaining": n} for name, n in self.steps]


def read_csv(path: Path) -> list[dict[str, str]]:
    """Righe di un CSV dei metadati.

    Raises:
        DataError: se il file manca.
    """
    if not path.is_file():
        raise DataError(f"file not found: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass
class CodeNetFiles:
    """Percorsi di CodeNet (``configs/dataset/codenet.yaml``)."""

    problem_list: Path
    metadata: Path
    descriptions: Path
    io_data: Path
    no_solutions: Path
    unverified: Path
    clusters: Path
    duplicates: Path

    @classmethod
    def from_spec(cls, spec: Any) -> CodeNetFiles:
        return cls(
            problem_list=spec.dirs["metadata"] / "problem_list.csv",
            metadata=spec.dirs["metadata"],
            descriptions=spec.dirs["problem_descriptions"],
            io_data=spec.dirs["io_data"],
            no_solutions=spec.file("no_solutions"),
            unverified=spec.file("unverified_accepted"),
            clusters=spec.file("identical_clusters"),
            duplicates=spec.dirs["duplicates"],
        )


def duplicate_index(root: Path) -> dict[str, int]:
    """Sottomissione → gruppo di quasi duplicati (tutti i linguaggi di ``derived/duplicates``)."""
    groups: list[frozenset[str]] = []
    for name in sorted(set(DUPLICATE_DIRS.values())):
        folder = root / name
        if not folder.is_dir():
            raise DataError(f"duplicates folder not found: {folder}")
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            groups += parse_groups(path.read_text(encoding="utf-8", errors="replace"), SID)
    return group_index(groups)


def find_candidates(
    files: CodeNetFiles, seed: int, excluded: Mapping[str, str]
) -> tuple[list[Candidate], Funnel, dict[str, Any]]:
    """Criteri sui metadati e sul testo, nell'ordine del ``Funnel``.

    Returns:
        I candidati in ordine di rango, i conteggi e statistiche diagnostiche.
    """
    funnel = Funnel()
    problems = read_csv(files.problem_list)
    source = {r["id"]: r.get("dataset", "") for r in problems}
    pids = sorted((p for p in source if PID.fullmatch(p)), key=lambda p: (rank_problem(seed, p), p))
    funnel.add("total (problem_list.csv)", len(pids))

    no_solutions = set(PID.findall(files.no_solutions.read_text(encoding="utf-8")))
    pids = [
        p
        for p in pids
        if p not in no_solutions
        and (files.io_data / p / "input.txt").is_file()
        and (files.io_data / p / "output.txt").is_file()
    ]
    funnel.add("input/output case present, not in no_solutions.txt", len(pids))

    excluded_pids = {k.split("/", 1)[1] for k in excluded}
    pids = [p for p in pids if p not in excluded_pids]
    funnel.add("not excluded (codenet_excluded.yaml, MCGMark oracle)", len(pids))

    # unverified_accepted_solutions.txt elenca problemi (una riga "pXXXXX" ciascuno): si escludono
    # i problemi e, se il file elencasse anche sottomissioni, nessuna di esse si usa mai.
    unverified_text = files.unverified.read_text(encoding="utf-8", errors="replace")
    unverified_pids = set(PID.findall(unverified_text))
    unverified = set(SID.findall(unverified_text))
    lines = [line.strip() for line in unverified_text.splitlines() if line.strip()]
    pids = [p for p in pids if p not in unverified_pids]
    funnel.add("not in unverified_accepted_solutions.txt", len(pids))

    texts: dict[str, Description] = {}
    for p in pids:
        path = files.descriptions / f"{p}.html"
        if path.is_file():
            texts[p] = describe(path.read_text(encoding="utf-8", errors="replace"))
    pids = [p for p in pids if p in texts and not texts[p].has_japanese and texts[p].text.strip()]
    funnel.add("description present and in English", len(pids))

    pids = [p for p in pids if texts[p].n_images == 0]
    funnel.add("no images in the description", len(pids))

    duplicates = duplicate_index(files.duplicates)
    candidates: list[Candidate] = []
    short: dict[str, int] = {str(lang): 0 for lang in LANG_NAMES}
    for p in pids:
        rows = read_csv(files.metadata / f"{p}.csv")
        subs = {
            lang: pick_submissions(seed, p, lang, rows, unverified, duplicates)
            for lang in LANG_NAMES
        }
        missing = [lang for lang, s in subs.items() if len(s) < N_PER_LANGUAGE]
        for lang in missing:
            short[str(lang)] += 1
        if missing:
            continue
        candidates.append(
            Candidate(
                pid=p,
                rank=rank_problem(seed, p),
                source=source.get(p, ""),
                submissions=subs,
                has_math=texts[p].has_math,
                has_table=texts[p].has_table,
            )
        )
    funnel.add(
        f"compatible versions: >= {N_PER_LANGUAGE} accepted, verified, distinct users, "
        "not near-duplicates, in each of the 4 languages",
        len(candidates),
    )
    stats = {
        "unverified_file": {
            "lines": len(lines),
            "problem_ids": len(unverified_pids),
            "submission_ids": len(unverified),
            "other_lines": sum(1 for x in lines if not PID.fullmatch(x) and not SID.search(x)),
        },
        "duplicate_submissions_indexed": len(duplicates),
        "problems_short_per_language": short,
        "japanese_descriptions": sum(1 for d in texts.values() if d.has_japanese),
    }
    return candidates, funnel, stats


def problem_clusters(files: CodeNetFiles) -> dict[str, int]:
    """Problema → cluster di ``identical_problem_clusters``."""
    text = files.clusters.read_text(encoding="utf-8", errors="replace")
    return group_index(parse_groups(text, PID))


def one_per_cluster(ranked: Sequence[str], clusters: Mapping[str, int]) -> list[str]:
    """Al più un problema per cluster: il primo nell'ordine dato."""
    seen: set[int] = set()
    kept = []
    for pid in ranked:
        cluster = clusters.get(pid)
        if cluster is not None:
            if cluster in seen:
                continue
            seen.add(cluster)
        kept.append(pid)
    return kept


# --------------------------------------------------------------------------- validazione e scelta
def dedup_codes(
    subs: Sequence[Submission], codes: Mapping[str, str | None]
) -> tuple[list[Submission], list[str]]:
    """Le prime ``N_PER_LANGUAGE`` sottomissioni con codice presente e impronta nuova.

    L'impronta (``code_fingerprint``) toglie i duplicati esatti a meno degli spazi in tutti i
    linguaggi; per JavaScript, che non ha ``derived/duplicates``, è l'unica deduplicazione.

    Returns:
        Le sottomissioni scelte e i motivi degli scarti (``missing``, ``duplicate``).
    """
    from bench.data.negatives import code_fingerprint

    chosen: list[Submission] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for sub in subs:
        code = codes.get(sub.submission_id)
        if code is None:
            dropped.append("missing")
            continue
        fp = code_fingerprint(code)
        if fp in seen:
            dropped.append("duplicate")
            continue
        seen.add(fp)
        chosen.append(sub)
        if len(chosen) == N_PER_LANGUAGE:
            break
    return chosen, dropped


def final_selection(
    candidates: Sequence[Candidate],
    valid: set[str],
    clusters: Mapping[str, int],
    n: int,
) -> tuple[list[Candidate], list[tuple[str, int]]]:
    """Primi ``n`` problemi idonei per rango, al più uno per cluster.

    Returns:
        I problemi scelti (ordinati per rango) e i conteggi dei passi (validazione, cluster,
        scelta). Se gli idonei sono meno di ``n`` i problemi scelti sono tutti gli idonei.
    """
    ranked = sorted(candidates, key=lambda c: (c.rank, c.pid))
    passed = [c for c in ranked if c.pid in valid]
    kept_pids = set(one_per_cluster([c.pid for c in passed], clusters))
    kept = [c for c in passed if c.pid in kept_pids]
    steps = [
        ("sandbox validation: canonical and 5 negatives pass in all 4 languages", len(passed)),
        ("at most one problem per identical_problem_clusters cluster", len(kept)),
        (
            f"selected: first {n} by derive_seed(global_seed, 'codenet-select', pid)",
            min(n, len(kept)),
        ),
    ]
    return kept[:n], steps
