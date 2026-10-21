"""``prepare_data`` del Livello 2 (CodeNet) e integrazione di sviluppo combinata L1+L2 (M9)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bench.config.schema import ExperimentConfig
from bench.data.codenet import LANG_NAMES
from bench.domain.enums import Language
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stages.prepare_data import (
    native_ref,
    pooled_integration_ref,
    problems_ref,
    split_ref,
)
from tests.conftest import rebuild
from tests.datafix import MIN_DEV, build_tiny_datasets, tiny_config

N_PROBLEMS, N_DEV = 4, 1
PROGRAMS = {
    Language.PYTHON: "n = int(input())\nfor i in range(n):\n    print(i * {k})\n",
    Language.JAVA: (
        "import java.util.*;\npublic class Main {{\n    public static void main(String[] a) {{\n"
        "        Scanner s = new Scanner(System.in);\n        int n = s.nextInt();\n"
        "        System.out.println(n * {k});\n    }}\n}}\n"
    ),
    Language.CPP: (
        "#include <iostream>\nint main() {{\n    int n;\n    std::cin >> n;\n"
        "    std::cout << n * {k} << std::endl;\n    return 0;\n}}\n"
    ),
    Language.JAVASCRIPT: (
        'const s = require("fs").readFileSync(0, "utf8");\nconst n = Number(s.trim());\n'
        "console.log(n * {k});\n"
    ),
}


def _codenet(root: Path) -> Path:
    """Mini CodeNet: descrizioni, sottomissioni estratte e selezione versionata."""
    base = root / "Project_CodeNet"
    problems = []
    for p in range(N_PROBLEMS):
        pid = f"p0{p:04d}"
        desc = base / "problem_descriptions"
        desc.mkdir(parents=True, exist_ok=True)
        (desc / f"{pid}.html").write_text(
            '<span class="lang-en"><p>Score : <var>100</var> points</p>'
            f"<p>Read <var>N</var> and print <var>N</var> times {p}.</p>"
            "<pre><var>N</var>\n</pre></span>",
            encoding="utf-8",
        )
        subs = {}
        for lang, name in LANG_NAMES.items():
            folder = base / "data" / pid / name
            folder.mkdir(parents=True, exist_ok=True)
            ext = {"python": "py", "java": "java", "cpp": "cpp", "javascript": "js"}[str(lang)]
            entries = []
            for i in range(6):
                sid = f"s{p:03d}{list(Language).index(lang)}{i:05d}"
                code = PROGRAMS[lang].format(k=p * 10 + i + 1) + "\n" * (i % 2)
                (folder / f"{sid}.{ext}").write_text(code, encoding="utf-8")
                entries.append({"submission_id": sid, "user_id": f"u{i}", "ext": ext,
                                "original_language": name})  # fmt: skip
            subs[str(lang)] = {"canonical": entries[0], "negatives": entries[1:]}
        problems.append({"problem_key": f"codenet/{pid}", "rank": p, "source": "AtCoder",
                         "submissions": subs})  # fmt: skip
    selected = root / "codenet_selected.yaml"
    funnel = [{"criterion": "total", "remaining": 99}, {"criterion": "selected", "remaining": 4}]
    selected.write_text(yaml.safe_dump({"funnel": funnel, "problems": problems}), encoding="utf-8")
    return selected


@pytest.fixture
def run_l2(cfg: ExperimentConfig) -> tuple[BenchmarkFacade, ExperimentConfig]:
    build_tiny_datasets(cfg.paths.datasets)
    selected = _codenet(cfg.paths.datasets)
    base = tiny_config(cfg)
    data = base.model_dump(mode="json")
    data["datasets"]["codenet"]["files"]["selected"] = str(selected)
    data["datasets"]["codenet"]["expected_rows"] = {"problems": N_PROBLEMS}
    data["split"]["families"]["codenet"] = {"dev": N_DEV, "total": N_PROBLEMS}
    tcfg = rebuild(base, datasets=data["datasets"], split=data["split"], levels=["L1", "L2"])
    facade = BenchmarkFacade(tcfg)
    report = facade.run_stage("prepare_data")
    assert report.ran == 2  # una cella per livello
    return facade, tcfg


def test_l2_artifacts_counts_and_split(run_l2: tuple[BenchmarkFacade, ExperimentConfig]) -> None:
    facade, _ = run_l2
    store = facade.store
    split = store.read_table(split_ref("L2"))
    assert len(split) == N_PROBLEMS and (split["split"] == "dev").sum() == N_DEV
    manifest = store.read_manifest(split_ref("L2"))
    assert manifest is not None and manifest.extra["selection"][-1]["remaining"] == 4
    dev_keys = set(split.loc[split["split"] == "dev", "problem_key"])
    for lang in Language:
        problems = store.read_table(problems_ref(lang, "L2"))
        assert len(problems) == N_PROBLEMS
        assert set(problems.loc[problems["split"] == "dev", "problem_key"]) == dev_keys  # I4
        assert problems["entry_point"].isna().all()
        assert not problems["prompt_text"].str.contains("Score").any()
        assert problems["prompt_text"].str.contains("```\nN\n```").all()
        assert (problems["loc_to_generate"] > 0).all()
        natives = store.read_table(native_ref(lang, "L2"))
        assert len(natives) == N_PROBLEMS * 5 and natives["sample_id"].is_unique
        assert sorted(set(natives["sample_index"])) == [0, 1, 2, 3, 4]
        assert set(natives["problem_key"]) == set(problems["problem_key"])
        # canonica mai fra i negativi
        assert not set(problems["canonical_solution"]) & set(natives["code"])


def test_pooled_integration_uses_the_combined_reference(
    run_l2: tuple[BenchmarkFacade, ExperimentConfig],
) -> None:
    facade, _ = run_l2
    store = facade.store
    for lang in Language:
        l1 = store.read_table(native_ref(lang))
        n_l1_dev = int((l1["split"] == "dev").sum())
        pooled = store.read_table(pooled_integration_ref(lang))
        assert len(pooled) == MIN_DEV - n_l1_dev - N_DEV * 5
        assert (pooled["split"] == "dev").all() and pooled["contamination_risk"].all()
        extra = store.read_manifest(pooled_integration_ref(lang)).extra  # type: ignore[union-attr]
        assert extra["reference_parts"] == {"l1_native_dev": n_l1_dev, "l2_native_dev": N_DEV * 5}
        assert 0.0 <= extra["ks_pool_vs_combined_natives"] <= 1.0
        assert extra["line_measure"].startswith("count_code_lines")


def test_l1_artifacts_are_untouched_and_rerun_is_skipped(
    run_l2: tuple[BenchmarkFacade, ExperimentConfig],
) -> None:
    facade, _ = run_l2
    path = facade.store.path_of(native_ref(Language.PYTHON))
    before = path.stat().st_mtime_ns
    assert facade.run_stage("prepare_data").skipped == 2
    assert path.stat().st_mtime_ns == before
