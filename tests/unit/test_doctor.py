"""Test di bench doctor con file system temporaneo ed esecutore di comandi finto."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bench_contracts
import pytest

from bench.config.schema import ExperimentConfig
from bench.doctor import (
    ApptainerCheck,
    AttackToolsCheck,
    CheckResult,
    CommandResult,
    DatasetsCheck,
    Doctor,
    DoctorCheck,
    DoctorContext,
    DoctorReport,
    EnvironmentsCheck,
    GpuCheck,
    ModelsCheck,
    PatchedCopiesCheck,
    PathsCheck,
    SlurmProfilesCheck,
    SourceryTokenCheck,
    Status,
    SubmodulesCheck,
    gpu_envs,
    load_cluster_profiles,
)
from tests.conftest import REPO_ROOT, FakeRunner, rebuild

PROFILES = load_cluster_profiles(REPO_ROOT / "configs" / "cluster")


def _ctx(
    cfg: ExperimentConfig,
    runner: FakeRunner | None = None,
    *,
    repo: Path | None = None,
    gpu: bool = False,
    environ: dict[str, str] | None = None,
    profiles: dict[str, object] | None = None,
) -> DoctorContext:
    return DoctorContext(
        cfg=cfg,
        repo=repo or cfg.paths.repo,
        profiles=profiles or PROFILES,  # type: ignore[arg-type]
        run=runner or FakeRunner(),
        gpu=gpu,
        environ=environ or {},
    )


def _statuses(results: list[CheckResult]) -> dict[str, Status]:
    return {r.name: r.status for r in results}


# ----------------------------------------------------------------------------- ambienti


def _fake_envs(cfg: ExperimentConfig, tmp_path: Path) -> ExperimentConfig:
    envs = {}
    for name in cfg.envs:
        exe = tmp_path / "envs" / name / "python"
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
        envs[name] = {"python": str(exe)}
    return rebuild(cfg, envs=envs)


def _probe(
    version: list[int], contracts_file: Path, contracts_version: str | None = None
) -> CommandResult:
    payload = {
        "python": version,
        "contracts_version": contracts_version or bench_contracts.__version__,
        "contracts_file": str(contracts_file),
    }
    return CommandResult(0, json.dumps(payload) + "\n", "")


def test_environments_missing_interpreters(cfg: ExperimentConfig) -> None:
    cfg = rebuild(cfg, envs={k: {"python": f"/nonexistent/{k}/python"} for k in cfg.envs})
    results = EnvironmentsCheck().run(_ctx(cfg))
    assert len(results) == 6
    assert all(r.status is Status.FAIL and "not found" in r.detail for r in results)


def test_environments_ok_and_wrong(cfg: ExperimentConfig, tmp_path: Path) -> None:
    cfg = _fake_envs(cfg, tmp_path)
    good_file = REPO_ROOT / "packages" / "bench-contracts" / "bench_contracts" / "__init__.py"
    runner = FakeRunner(
        responses={
            (str(cfg.envs["bench-core"].python), "-c"): _probe([3, 11, 16], good_file),
            (str(cfg.envs["sweet"].python), "-c"): _probe([3, 10, 21], good_file),
            (str(cfg.envs["acw"].python), "-c"): _probe([3, 8, 10], good_file),
            (str(cfg.envs["stone"].python), "-c"): _probe(
                [3, 9, 19], tmp_path / "elsewhere" / "x.py"
            ),
            (str(cfg.envs["promptmark"].python), "-c"): _probe([3, 10, 21], good_file, "0.9"),
            (str(cfg.envs["mcgmark"].python), "-c"): CommandResult(
                1, "", "ModuleNotFoundError: No module named 'bench_contracts'"
            ),
        }
    )
    results = _statuses(EnvironmentsCheck().run(_ctx(cfg, runner)))
    assert results["bench-core"] is Status.OK
    assert results["sweet"] is Status.OK
    assert results["acw"] is Status.FAIL  # Python 3.8
    assert results["stone"] is Status.FAIL  # installazione da un altro percorso
    assert results["promptmark"] is Status.FAIL  # versione dei contratti diversa
    assert results["mcgmark"] is Status.FAIL
    # I sottoprocessi girano senza PYTHONPATH.
    assert all(call[1] == "-c" for call in runner.calls)


def test_bench_core_must_be_311(cfg: ExperimentConfig, tmp_path: Path) -> None:
    cfg = _fake_envs(cfg, tmp_path)
    good_file = REPO_ROOT / "packages" / "bench-contracts" / "bench_contracts" / "__init__.py"
    runner = FakeRunner(fallback=lambda cmd: _probe([3, 10, 21], good_file))
    results = _statuses(EnvironmentsCheck().run(_ctx(cfg, runner)))
    assert results["bench-core"] is Status.FAIL
    assert results["sweet"] is Status.OK


# ----------------------------------------------------------------------------- submodule e copie


def _git_runner(
    repo: Path, links: dict[str, str], heads: dict[str, str], dirty: set[str] = frozenset()
) -> FakeRunner:  # type: ignore[assignment]
    responses: dict[tuple[str, ...], CommandResult] = {
        ("git", "rev-parse", "HEAD"): CommandResult(0, "a" * 40 + "\n", ""),
        ("git", "status", "--porcelain", "--untracked-files=no"): CommandResult(0, "", ""),
        ("git", "config", "-f", ".gitmodules"): CommandResult(
            0, "".join(f"submodule.{p}.path {p}\n" for p in links), ""
        ),
    }
    for path, link in links.items():
        responses[("git", "ls-files", "--stage", "--", path)] = CommandResult(
            0, f"160000 {link} 0\t{path}\n", ""
        )
        sub = str(repo / path)
        responses[("git", "-C", sub, "rev-parse", "HEAD")] = CommandResult(
            0, heads.get(path, link) + "\n", ""
        )
        responses[("git", "-C", sub, "status", "--porcelain")] = CommandResult(
            0, " M file\n" if path in dirty else "", ""
        )
    return FakeRunner(responses=responses)


def _make_submodules(repo: Path, n: int) -> dict[str, str]:
    links = {}
    for i in range(n):
        path = f"third_party/sub{i}"
        (repo / path).mkdir(parents=True)
        (repo / path / ".git").write_text("gitdir: x", encoding="utf-8")
        links[path] = hashlib.sha1(path.encode()).hexdigest()
    return links


def test_submodules_ok_and_problems(cfg: ExperimentConfig, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    links = _make_submodules(repo, 8)
    paths = sorted(links)
    runner = _git_runner(repo, links, heads={paths[0]: "f" * 40}, dirty={paths[1]})
    results = _statuses(SubmodulesCheck().run(_ctx(cfg, runner, repo=repo)))
    assert results["count"] is Status.OK
    assert results["repository"] is Status.OK
    assert results[paths[0]] is Status.FAIL
    assert results[paths[1]] is Status.FAIL
    assert all(results[p] is Status.OK for p in paths[2:])


def test_submodule_count_mismatch(cfg: ExperimentConfig, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    links = _make_submodules(repo, 9)
    results = _statuses(SubmodulesCheck().run(_ctx(cfg, _git_runner(repo, links, {}), repo=repo)))
    assert results["count"] is Status.FAIL


def _make_patched(repo: Path, method: str, submodule: str, commit: str, patch_text: str) -> None:
    patch = repo / "patches" / method / "0000-x.patch"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(patch_text, encoding="utf-8")
    patched = repo / "build" / "patched"
    (patched / method).mkdir(parents=True, exist_ok=True)
    (patched / f"{method}.source_commit").write_text(commit + "\n", encoding="utf-8")
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    (patched / f"{method}.patches.sha256").write_text(
        f"{digest}  patches/{method}/0000-x.patch\n", encoding="utf-8"
    )


def test_patched_copies(cfg: ExperimentConfig, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    links = {
        m.submodule: hashlib.sha1(m.name.encode()).hexdigest() for m in cfg.methods_catalog.values()
    }
    for m in cfg.methods_catalog.values():
        if m.name != "mcgmark":
            _make_patched(repo, m.name, m.submodule, links[m.submodule], f"patch {m.name}\n")
    # sweet: patch modificata dopo la copia; stone: copia da un commit diverso.
    (repo / "patches" / "sweet" / "0000-x.patch").write_text("changed\n", encoding="utf-8")
    (repo / "build" / "patched" / "stone.source_commit").write_text("0" * 40, encoding="utf-8")
    results = _statuses(
        PatchedCopiesCheck().run(_ctx(cfg, _git_runner(repo, links, {}), repo=repo))
    )
    assert results["acw"] is Status.OK
    assert results["promptmark"] is Status.OK
    assert results["sweet"] is Status.FAIL
    assert results["stone"] is Status.FAIL
    assert results["mcgmark"] is Status.FAIL


# --------------------------------------------------- apptainer, modelli, dataset


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (CommandResult(0, "apptainer version 1.1.9\n", ""), Status.OK),
        (CommandResult(0, "apptainer version 1.2.5\n", ""), Status.FAIL),
        (CommandResult(127, "", "command not found: apptainer"), Status.FAIL),
    ],
)
def test_apptainer(cfg: ExperimentConfig, result: CommandResult, expected: Status) -> None:
    runner = FakeRunner(responses={("apptainer", "--version"): result})
    results = _statuses(ApptainerCheck().run(_ctx(cfg, runner)))
    assert results["version"] is expected
    assert results["sandbox image"] is Status.SKIP


def test_models(cfg: ExperimentConfig) -> None:
    qwen = cfg.models_catalog["qwen25_coder_7b"]
    qwen.path.mkdir(parents=True)
    (qwen.path / "config.json").write_text("{}", encoding="utf-8")
    llama = cfg.models_catalog["llama31_8b_attacker"]
    llama.path.mkdir(parents=True)
    results = _statuses(ModelsCheck().run(_ctx(cfg)))
    assert results["qwen25_coder_7b"] is Status.OK
    assert results["llama31_8b_attacker"] is Status.FAIL  # manca config.json
    assert results["starcoder2_7b"] is Status.FAIL


def test_datasets(cfg: ExperimentConfig) -> None:
    for path in cfg.datasets["humanevalplus"].files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    results = _statuses(DatasetsCheck().run(_ctx(cfg)))
    assert results["humanevalplus"] is Status.OK
    assert results["codesearchnet"] is Status.FAIL
    assert results["tests4py"] is Status.SKIP
    # ClassEval sta nel submodule del repository.
    assert results["classeval"] is (
        Status.OK
        if (cfg.paths.repo / "third_party/ClassEval/data/ClassEval_data.json").is_file()
        else Status.FAIL
    )


# --------------------------------------------------- segreti, slurm, percorsi, gpu


def test_sourcery_token_presence_only(cfg: ExperimentConfig) -> None:
    secret = "super-secret-token-value"
    ctx = _ctx(cfg, environ={"SOURCERY_TOKEN": secret})
    results = SourceryTokenCheck().run(ctx)
    assert results[0].status is Status.OK
    report = DoctorReport(results)
    assert secret not in report.render()
    assert secret not in report.to_json()
    assert SourceryTokenCheck().run(_ctx(cfg))[0].status is Status.WARN


def test_slurm_real_profiles_are_coherent(cfg: ExperimentConfig) -> None:
    results = SlurmProfilesCheck().run(_ctx(cfg))
    assert not [r for r in results if r.status is Status.FAIL], results
    names = _statuses(results)
    assert names["slurm_gpu"] is Status.OK
    assert names["slurm_cpu"] is Status.OK
    assert names["policy GPU_NVIDIA"] is Status.OK
    assert all(names[f"timeout {m}"] is Status.OK for m in cfg.methods_catalog)


def test_slurm_detects_bad_profiles_and_timeouts(cfg: ExperimentConfig) -> None:
    profiles = dict(PROFILES)
    profiles["slurm_cpu"] = profiles["slurm_cpu"].model_copy(update={"gres": "gpu:1"})
    profiles["slurm_gpu"] = profiles["slurm_gpu"].model_copy(update={"timeout_min": 200})
    methods = cfg.model_dump(mode="json")["methods_catalog"]
    methods["stone"]["worker_timeout_s"] = 13000
    cfg = rebuild(cfg, methods_catalog=methods)
    results = _statuses(SlurmProfilesCheck().run(_ctx(cfg, profiles=profiles)))  # type: ignore[arg-type]
    assert results["slurm_cpu"] is Status.FAIL
    assert results["policy CPU"] is Status.FAIL
    assert results["timeout stone"] is Status.FAIL


def test_paths_writable(cfg: ExperimentConfig) -> None:
    results = _statuses(PathsCheck().run(_ctx(cfg)))
    assert results == {"artifacts": Status.OK, "tmp": Status.OK}
    assert cfg.paths.artifacts.is_dir()


def test_gpu_checks_only_on_request(cfg: ExperimentConfig, tmp_path: Path) -> None:
    runner = FakeRunner()
    assert GpuCheck().run(_ctx(cfg, runner))[0].status is Status.SKIP
    assert runner.calls == []
    cfg = _fake_envs(cfg, tmp_path)
    ok = CommandResult(
        0, json.dumps({"cuda": True, "cuda_version": "12.1", "devices": 1, "torch": "2.4.1"}), ""
    )
    no = CommandResult(
        0, json.dumps({"cuda": False, "cuda_version": None, "devices": 0, "torch": "2.4.1"}), ""
    )
    no_torch = CommandResult(1, "", "ModuleNotFoundError: No module named 'torch'")

    def answer(cmd: tuple[str, ...]) -> CommandResult:
        if "acw" in cmd[0]:
            return no_torch  # come sul cluster: l'ambiente acw non ha torch
        return no if "mcgmark" in cmd[0] else ok

    runner = FakeRunner(fallback=answer)
    results = _statuses(GpuCheck().run(_ctx(cfg, runner, gpu=True)))
    assert results["bench-core"] is Status.OK
    assert results["sweet"] is Status.OK
    assert results["mcgmark"] is Status.FAIL  # metodo GPU senza CUDA
    # ACW è solo CPU (resources): non si prova nemmeno a importare torch.
    assert results["acw"] is Status.SKIP
    assert not any("acw" in call[0] for call in runner.calls)


def test_gpu_envs_follow_resource_rules(cfg: ExperimentConfig) -> None:
    assert gpu_envs(cfg) == {"bench-core", "sweet", "stone", "promptmark", "mcgmark"}


# ----------------------------------------------------------------------------- report


def test_report_render_and_exit_code(cfg: ExperimentConfig) -> None:
    class Crashing(DoctorCheck):
        category = "broken"

        def run(self, ctx: DoctorContext) -> list[CheckResult]:
            raise RuntimeError("bug")

    report = Doctor(_ctx(cfg), checks=[PathsCheck, AttackToolsCheck, Crashing]).run()
    text = report.render()
    assert "[paths]" in text and "[attack tools]" in text and "[broken]" in text
    assert "check crashed" in text
    assert "summary: 2 OK, 0 WARN, 1 FAIL, 1 SKIP -> FAILED" in text
    assert report.exit_code == 1
    data = json.loads(report.to_json())
    assert data["exit_code"] == 1
    assert {r["status"] for r in data["results"]} == {"OK", "FAIL", "SKIP"}


def test_report_ok_exit_code() -> None:
    report = DoctorReport([CheckResult("c", "n", Status.OK), CheckResult("c", "w", Status.WARN)])
    assert report.exit_code == 0
    assert report.render().rstrip().endswith("-> OK")
