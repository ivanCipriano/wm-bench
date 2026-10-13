"""CodeNet per il Livello 2 (Milestone 9): criteri di selezione, testo dei problemi, I/O."""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

import pytest

from bench.config.schema import ExperimentConfig
from bench.data.codenet import (
    N_PER_LANGUAGE,
    Candidate,
    CodeNetFiles,
    Submission,
    compatible,
    dedup_codes,
    describe,
    final_selection,
    find_candidates,
    one_per_cluster,
    parse_groups,
    pick_submissions,
    problem_clusters,
    rank_problem,
)
from bench.domain.enums import Language
from bench.execution import io_check

SEED = 20261001
FIELDS = [
    "submission_id",
    "problem_id",
    "user_id",
    "date",
    "language",
    "original_language",
    "filename_ext",
    "status",
    "cpu_time",
    "memory",
    "code_size",
    "accuracy",
]
VERSIONS = {
    Language.PYTHON: ("Python", "Python (3.8.2)", "py"),
    Language.JAVA: ("Java", "Java (OpenJDK 11.0.6)", "java"),
    Language.CPP: ("C++", "C++ (GCC 9.2.1)", "cpp"),
    Language.JAVASCRIPT: ("JavaScript", "JavaScript (Node.js 12.16.1)", "js"),
}


# --------------------------------------------------------------------------- regole elementari
@pytest.mark.parametrize(
    ("lang", "version", "ok"),
    [
        (Language.PYTHON, "Python (3.4.3)", True),
        (Language.PYTHON, "Python3", True),
        (Language.PYTHON, "PyPy3 (2.4.0)", True),
        (Language.PYTHON, "Python (2.7.6)", False),
        (Language.PYTHON, "PyPy2 (5.6.0)", False),
        (Language.PYTHON, "Python", False),
        (Language.CPP, "C++14 (GCC 5.4.1)", True),
        (Language.CPP, "C++14 (Clang 3.8.0)", True),
        (Language.CPP, "C++ (MSVC)", False),
        (Language.JAVA, "Java8 (OpenJDK 1.8.0)", True),
        (Language.JAVASCRIPT, "JavaScript (node.js v5.12)", True),
        (Language.JAVASCRIPT, "JavaScript", True),
        (Language.JAVASCRIPT, "JavaScript (SpiderMonkey 38)", False),
    ],
)
def test_compatible_versions(lang: Language, version: str, ok: bool) -> None:
    assert compatible(lang, version) is ok


def test_groups_by_line_or_by_block() -> None:
    by_line = "p00001,p00002\np00003\np00004 p00005\n"
    assert parse_groups(by_line, re.compile(r"p\d{5}")) == [
        frozenset({"p00001", "p00002"}),
        frozenset({"p00004", "p00005"}),
    ]
    by_block = "a/s000000001.py\na/s000000002.py\n\na/s000000003.py\n\nb/s000000004.py\n"
    assert parse_groups(by_block, re.compile(r"s\d{9}")) == [
        frozenset({"s000000001", "s000000002"})
    ]


def test_one_per_cluster_keeps_the_first_in_order() -> None:
    clusters = {"p1": 0, "p2": 0, "p3": 1}
    assert one_per_cluster(["p2", "p4", "p1", "p3"], clusters) == ["p2", "p4", "p3"]


# --------------------------------------------------------------------------- descrizioni
ATCODER = """<span class="lang">
<span class="lang-ja"><p>日本語の問題文 <var>N</var></p></span>
<span class="lang-en">
<div class="part"><section><h3>Problem Statement</h3>
<p>Given are <var>N</var> integers <var>A_1, \\ldots, A_N</var>.</p>
<ul><li><var>1 \\leq N \\leq 10^5</var></li><li>All values are integers.</li></ul>
<table><tr><th>x</th><th>y</th></tr><tr><td>1</td><td>2</td></tr></table>
</section></div>
<h3>Sample Input 1</h3><pre>3
1  2 3
</pre>
</span></span>"""


def test_atcoder_keeps_only_english_and_renders_math_tables_and_pre() -> None:
    d = describe(ATCODER)
    assert not d.has_japanese
    assert d.has_math and d.has_table and d.n_images == 0
    assert "Given are $N$ integers $A_1, \\ldots, A_N$." in d.text
    assert "- $1 \\leq N \\leq 10^5$" in d.text
    assert "x | y\n1 | 2" in d.text
    assert "```\n3\n1  2 3\n```" in d.text  # spazi dentro <pre> invariati


def test_japanese_only_and_images_are_detected() -> None:
    assert describe("<p>整数を出力せよ</p>").has_japanese
    d = describe('<h1>Title</h1><p>See <img src="a.png"> and &lt;tag&gt;</p>')
    assert d.n_images == 1 and "[image]" in d.text and "<tag>" in d.text


# --------------------------------------------------------------------------- sottomissioni
def _row(sid: str, user: str, lang: Language, **kw: str) -> dict[str, str]:
    name, version, ext = VERSIONS[lang]
    row = {
        "submission_id": sid,
        "user_id": user,
        "language": name,
        "original_language": version,
        "filename_ext": ext,
        "status": "Accepted",
    }
    row.update(kw)
    return row


def test_pick_submissions_filters_and_is_deterministic() -> None:
    rows = [_row(f"s{i:09d}", f"u{i}", Language.PYTHON) for i in range(12)]
    rows.append(_row("s000000100", "u0", Language.PYTHON))  # utente ripetuto
    rows.append(_row("s000000101", "u101", Language.PYTHON, status="Wrong Answer"))
    rows.append(_row("s000000102", "u102", Language.PYTHON, original_language="Python (2.7.6)"))
    rows.append(_row("s000000103", "u103", Language.JAVA))
    rows.append(_row("s000000104", "u104", Language.PYTHON))  # non verificata
    dup = {"s000000001": 7, "s000000002": 7}  # quasi duplicati: al più uno dei due
    args = (SEED, "p00001", Language.PYTHON, rows, {"s000000104"}, dup)
    picked = pick_submissions(*args, limit=20)
    ids = [s.submission_id for s in picked]
    assert ids == [s.submission_id for s in pick_submissions(*args, limit=20)]
    assert {"s000000101", "s000000102", "s000000103", "s000000104"}.isdisjoint(ids)
    assert len({s.user_id for s in picked}) == len(picked)
    assert len({"s000000001", "s000000002"} & set(ids)) == 1
    assert len(pick_submissions(*args, limit=3)) == 3


def test_dedup_codes_skips_missing_and_identical_code() -> None:
    subs = [
        Submission(f"s{i:09d}", f"u{i}", Language.JAVASCRIPT, "JavaScript", "js") for i in range(9)
    ]
    codes: dict[str, str | None] = {s.submission_id: f"print({i})" for i, s in enumerate(subs)}
    codes["s000000001"] = None
    codes["s000000002"] = "print( 0 )".replace(" ", "")  # uguale a s000000000 a meno di spazi
    chosen, dropped = dedup_codes(subs, codes)
    assert len(chosen) == N_PER_LANGUAGE
    assert dropped == ["missing", "duplicate"]
    assert [s.submission_id for s in chosen][:2] == ["s000000000", "s000000003"]


# --------------------------------------------------------------------------- albero finto
def _tree(tmp_path: Path) -> CodeNetFiles:
    root = tmp_path / "Project_CodeNet"
    meta, desc, io = root / "metadata", root / "problem_descriptions", root / "derived" / "io"
    dup = root / "derived" / "duplicates"
    for d in (meta, desc, dup / "Python", dup / "Java", dup / "C++"):
        d.mkdir(parents=True)
    problems = {
        "p00001": "ok",
        "p00002": "ok",  # stesso cluster di p00001
        "p00003": "no_io",
        "p00004": "japanese",
        "p00005": "image",
        "p00006": "few_js",
        "p00007": "ok",
        "p00008": "no_solutions",
        "p00009": "unverified",
    }
    with (meta / "problem_list.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "name", "dataset"])
        for pid in problems:
            writer.writerow([pid, pid, "AtCoder"])
    for pid, kind in problems.items():
        text = {"japanese": "<p>入力</p>", "image": '<p>Look <img src="x.png"></p>'}.get(
            kind, f"<p>Problem {pid}: read <var>N</var>.</p>"
        )
        (desc / f"{pid}.html").write_text(text, encoding="utf-8")
        if kind != "no_io":
            (io / pid).mkdir(parents=True)
            (io / pid / "input.txt").write_text("1\n", encoding="utf-8")
            (io / pid / "output.txt").write_text("2\n", encoding="utf-8")
        with (meta / f"{pid}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            for lang in Language:
                n = 3 if (kind == "few_js" and lang is Language.JAVASCRIPT) else 8
                for i in range(n):
                    sid = f"s{int(pid[1:]):04d}{list(Language).index(lang)}{i:04d}"
                    writer.writerow(_row(sid, f"u{i}", lang, problem_id=pid))
    (root / "derived" / "no_solutions.txt").write_text("p00008\n", encoding="utf-8")
    (root / "derived" / "unverified.txt").write_text("p00009\n", encoding="utf-8")
    (dup / "identical_problem_clusters").write_text("p00001,p00002\n", encoding="utf-8")
    return CodeNetFiles(
        problem_list=meta / "problem_list.csv",
        metadata=meta,
        descriptions=desc,
        io_data=io,
        no_solutions=root / "derived" / "no_solutions.txt",
        unverified=root / "derived" / "unverified.txt",
        clusters=dup / "identical_problem_clusters",
        duplicates=dup,
    )


def test_funnel_counts_each_criterion_in_order(tmp_path: Path) -> None:
    files = _tree(tmp_path)
    excluded = {"codenet/p00007": "oracle_mcgmark"}
    candidates, funnel, stats = find_candidates(files, SEED, excluded)
    assert [n for _, n in funnel.steps] == [9, 7, 6, 5, 4, 3, 2]
    assert stats["unverified_file"] == {
        "lines": 1,
        "problem_ids": 1,
        "submission_ids": 0,
        "other_lines": 0,
    }
    assert sorted(c.pid for c in candidates) == ["p00001", "p00002"]
    assert stats["problems_short_per_language"]["javascript"] == 1
    assert [c.rank for c in candidates] == sorted(c.rank for c in candidates)
    assert all(len(s) >= N_PER_LANGUAGE for c in candidates for s in c.submissions.values())
    restored = Candidate.from_dict(candidates[0].as_dict())
    assert restored == candidates[0]

    clusters = problem_clusters(files)
    selected, steps = final_selection(candidates, {"p00001", "p00002"}, clusters, n=250)
    assert len(selected) == 1  # stesso cluster: resta il primo per rango
    first = min(("p00001", "p00002"), key=lambda p: rank_problem(SEED, p))
    assert selected[0].pid == first
    assert [n for _, n in steps] == [2, 1, 1]
    # Se il primo per rango non supera la validazione, resta l'altro membro del cluster.
    other = ({"p00001", "p00002"} - {first}).pop()
    selected, _ = final_selection(candidates, {other}, clusters, n=250)
    assert [c.pid for c in selected] == [other]


# --------------------------------------------------------------------------- confronto I/O
def _check(tmp_path: Path, program: str, expected: str, stdin: str = "1 2\n") -> tuple[int, str]:
    (tmp_path / "in.txt").write_text(stdin, encoding="utf-8")
    (tmp_path / "out.txt").write_text(expected, encoding="utf-8")
    (tmp_path / "main.py").write_text(program, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            io_check.__file__,
            str(tmp_path / "in.txt"),
            str(tmp_path / "out.txt"),
            "--",
            sys.executable,
            str(tmp_path / "main.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stderr


def test_io_check_ignores_trailing_spaces_only(tmp_path: Path) -> None:
    sum_prog = "a, b = map(int, input().split())\nprint(a + b, ' ')\nprint()\n"
    assert _check(tmp_path, sum_prog, "3\n")[0] == 0
    code, err = _check(tmp_path, "print(4)\n", "3\n")
    assert code == 1 and "Assertion: output mismatch" in err
    code, err = _check(tmp_path, "print( 3)\nprint(1)\n", "3\n")
    assert code == 1  # una riga in più non è uno spazio finale


def test_io_check_reports_runtime_errors(tmp_path: Path) -> None:
    code, err = _check(tmp_path, "raise ValueError('boom')\n", "3\n")
    assert code != 0 and "ValueError" in err and "Assertion" not in err


def test_normalize_output_is_shared() -> None:
    from bench.data import codenet

    assert codenet.normalize_output is io_check.normalize_output
    assert io_check.normalize_output("1 \r\n2\t\n\n") == "1\n2"


# --------------------------------------------------------------------------- executor
def test_codenet_executor_job_uses_the_runner_in_the_image(
    cfg: ExperimentConfig, tmp_path: Path
) -> None:
    from bench.domain.enums import Level
    from bench.domain.models import Problem
    from bench.execution.executors import EXECUTORS, CodeNetExecutor, SampleToRun, executor_name
    from bench.execution.sandbox import LocalSandbox

    (tmp_path / "p00001").mkdir()
    (tmp_path / "p00001" / "input.txt").write_bytes(b"1\n")
    (tmp_path / "p00001" / "output.txt").write_bytes(b"2\n")
    assert EXECUTORS.get(executor_name("codenet")) is CodeNetExecutor
    ex = CodeNetExecutor(cfg, LocalSandbox(), Language.JAVA, tmp_path / "w", io_root=tmp_path)
    problem = Problem(
        problem_key="codenet/p00001",
        dataset="codenet",
        level=Level.L2,
        language=Language.JAVA,
        split=None,
        prompt_text="",
        entry_point=None,
        canonical_solution=None,
        test_ref="p00001",
        contamination_risk=False,
        loc_to_generate=None,
    )
    job, files = ex.job(problem, [SampleToRun("a", problem.problem_key, 0, "class Main {}")])
    assert job["rule"] == "syntax_java" and job["compile_cmd"] == ["javac", "Main.java"]
    assert job["run_cmd"][:5] == [
        "python3",
        "../io_check.py",
        "../input.txt",
        "../expected.txt",
        "--",
    ]
    assert job["run_cmd"][5:] == ["java", "-cp", ".", "Main"]
    assert files["input.txt"] == b"1\n" and files["expected.txt"] == b"2\n"
    assert files["io_check.py"] == Path(io_check.__file__).read_bytes()


def test_prompt_builder_accepts_codenet_without_entry_point(cfg: ExperimentConfig) -> None:
    from bench.config.builder import repo_root
    from bench.domain.enums import Level
    from bench.domain.models import Problem
    from bench.generation.prompt_builder import PromptBuilder

    builder = PromptBuilder(repo_root() / "configs" / "prompt" / "drafts", "sys", "x")
    for lang in Language:
        problem = Problem(
            problem_key="codenet/p00001",
            dataset="codenet",
            level=Level.L2,
            language=lang,
            split=None,
            prompt_text="Read N and print N.",
            entry_point=None,
            canonical_solution=None,
            test_ref="p00001",
            contamination_risk=False,
            loc_to_generate=None,
        )
        text = builder.render_user(problem)
        assert text.endswith("Read N and print N.\n")
        assert "standard input" in text and "standard output" in text
    java = Problem(
        problem_key="codenet/p00001",
        dataset="codenet",
        level=Level.L2,
        language=Language.JAVA,
        split=None,
        prompt_text="x",
        entry_point=None,
        canonical_solution=None,
        test_ref="p00001",
        contamination_risk=False,
        loc_to_generate=None,
    )
    assert "public class Main" in builder.render_user(java)


def test_codenet_templates_share_the_same_structure() -> None:
    from bench.config.builder import repo_root

    drafts = repo_root() / "configs" / "prompt" / "drafts"
    texts = {lang: (drafts / f"codenet_{lang}.j2").read_text(encoding="utf-8") for lang in Language}
    lines = {lang: t.split("\n") for lang, t in texts.items()}
    assert len({len(v) for v in lines.values()}) == 1
    assert len({v[1] for v in lines.values()}) == 1  # frase su stdin/stdout identica
    assert all(v[-2] == "{{ prompt }}" and v[-3] == "" for v in lines.values())


def test_input_format_in_pre_keeps_spaces_and_newlines() -> None:
    html = (
        '<span class="lang-en"><p>Read <var>N</var>.</p>'
        "<pre><var>N</var>\n<var>A_1</var> <var>A_2</var> <var>...</var> <var>A_N</var>\n</pre>"
        "<p>\ufeffPrint <var>x</var><var>y</var>.</p></span>"
    )
    text = describe(html).text
    assert "```\nN\nA_1 A_2 ... A_N\n```" in text
    assert "Read $N$." in text and "Print $xy$." in text and "\ufeff" not in text
