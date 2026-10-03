"""Diagnostica dell'installazione: ``bench doctor`` (SPEC §16.4, cluster_info).

Ogni controllo è una strategia (``DoctorCheck``) che restituisce uno o più
``CheckResult``. I controlli che richiedono una GPU girano solo con ``gpu=True``,
perché il doctor si lancia di norma dal nodo di login.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

import bench_contracts
from omegaconf import OmegaConf

from bench.config.builder import config_dir
from bench.config.resources import ResourceClass, ResourcePolicy, validate_profile
from bench.config.schema import ClusterProfile, ExperimentConfig
from bench.domain.errors import ConfigError

# Valori attesi (fonti: cluster_info §3, §5, §9; ADR-002).
EXPECTED_SUBMODULES = 8
EXPECTED_APPTAINER = "1.1.9"
BENCH_CORE_PYTHON = (3, 11)
MIN_WORKER_PYTHON = (3, 9)
ENV_PROBE_TIMEOUT_S = 60.0
GPU_PROBE_TIMEOUT_S = 300.0
_GITLINK_MODE = "160000"
_LS_FILES_MIN_FIELDS = 2
_MAX_SHOWN = 3

_ENV_PROBE = (
    "import json, sys, bench_contracts as b; "
    "print(json.dumps({'python': list(sys.version_info[:3]), "
    "'contracts_version': b.__version__, 'contracts_file': b.__file__}))"
)
_GPU_PROBE = (
    "import json, torch; "
    "print(json.dumps({'cuda': torch.cuda.is_available(), 'cuda_version': torch.version.cuda, "
    "'devices': torch.cuda.device_count(), 'torch': torch.__version__}))"
)


class Status(StrEnum):
    """Esito di un controllo."""

    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class CheckResult:
    """Esito di un singolo controllo."""

    category: str
    name: str
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class CommandResult:
    """Esito di un comando esterno."""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float, Mapping[str, str] | None, Path | None], CommandResult]


def run_command(
    cmd: Sequence[str],
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> CommandResult:
    """Esegue un comando senza mai sollevare: 127 se non trovato, 124 in caso di timeout."""
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            cwd=cwd,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", f"timeout after {timeout:.0f}s")
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


@dataclass
class DoctorContext:
    """Dipendenze dei controlli (iniettabili nei test)."""

    cfg: ExperimentConfig
    repo: Path
    profiles: dict[str, ClusterProfile]
    run: Runner = run_command
    gpu: bool = False
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))

    def clean_env(self) -> dict[str, str]:
        """Ambiente per i sottoprocessi senza ``PYTHONPATH``/``PYTHONHOME``."""
        return {k: v for k, v in self.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}


def load_cluster_profiles(cluster_dir: Path) -> dict[str, ClusterProfile]:
    """Carica tutti i profili ``configs/cluster/*.yaml``.

    Raises:
        ConfigError: se la cartella non contiene profili o un profilo non è valido.
    """
    paths = sorted(cluster_dir.glob("*.yaml"))
    if not paths:
        raise ConfigError(f"no cluster profiles in {cluster_dir}")
    profiles: dict[str, ClusterProfile] = {}
    for path in paths:
        data = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
        try:
            profile = ClusterProfile.model_validate(data)
        except ValueError as exc:
            raise ConfigError(f"invalid cluster profile {path.name}: {exc}") from exc
        profiles[profile.name] = profile
    return profiles


def build_doctor_context(
    cfg: ExperimentConfig, gpu: bool = False, run: Runner = run_command
) -> DoctorContext:
    """Costruisce il contesto del doctor dalla configurazione."""
    repo = cfg.paths.repo
    return DoctorContext(
        cfg=cfg,
        repo=repo,
        # Profili del codice in esecuzione: sono quelli che userà ``bench submit``.
        profiles=load_cluster_profiles(config_dir() / "cluster"),
        run=run,
        gpu=gpu,
    )


class DoctorCheck(ABC):
    """Strategia di controllo."""

    category: ClassVar[str]

    @abstractmethod
    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        """Esegue il controllo."""

    def result(self, name: str, status: Status, detail: str = "") -> CheckResult:
        """Crea un risultato della categoria del controllo."""
        return CheckResult(self.category, name, status, detail)


def _short(path: Path, base: Path) -> str:
    """Percorso relativo a ``base`` (con ``<base>/`` davanti) se vi è contenuto."""
    try:
        return f"<{base.name}>/{path.relative_to(base).as_posix()}"
    except ValueError:
        return str(path)


def _parse_json_line(text: str) -> dict[str, object] | None:
    for line in reversed(text.strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


# ----------------------------------------------------------------------------- controlli


class EnvironmentsCheck(DoctorCheck):
    """Interpreti dei 6 ambienti e import di ``bench_contracts`` (cluster_info §3)."""

    category = "environments"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        expected_pkg = (ctx.repo / "packages" / "bench-contracts").resolve()
        for name, spec in sorted(ctx.cfg.envs.items()):
            if not spec.python.exists():
                results.append(
                    self.result(name, Status.FAIL, f"interpreter not found: {spec.python}")
                )
                continue
            proc = ctx.run(
                [str(spec.python), "-c", _ENV_PROBE],
                ENV_PROBE_TIMEOUT_S,
                ctx.clean_env(),
                Path("/"),
            )
            info = _parse_json_line(proc.stdout) if proc.returncode == 0 else None
            if info is None:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
                results.append(
                    self.result(name, Status.FAIL, f"import bench_contracts failed: {tail[0]}")
                )
                continue
            version = tuple(int(x) for x in info["python"])  # type: ignore[attr-defined]
            problems: list[str] = []
            if name == "bench-core" and version[:2] != BENCH_CORE_PYTHON:
                problems.append(f"Python {version} != 3.11")
            if version[:2] < MIN_WORKER_PYTHON:
                problems.append(f"Python {version} < 3.9")
            if info["contracts_version"] != bench_contracts.__version__:
                problems.append(
                    f"bench_contracts {info['contracts_version']} != {bench_contracts.__version__}"
                )
            contracts_file = Path(str(info["contracts_file"])).resolve()
            if not contracts_file.is_relative_to(expected_pkg):
                problems.append(
                    f"bench_contracts imported from {contracts_file}, not from this repo"
                )
            pyver = ".".join(str(x) for x in version)
            if problems:
                results.append(self.result(name, Status.FAIL, "; ".join(problems)))
            else:
                results.append(
                    self.result(
                        name,
                        Status.OK,
                        f"Python {pyver}, bench_contracts {info['contracts_version']}",
                    )
                )
        return results


def _gitlinks(ctx: DoctorContext) -> dict[str, str]:
    """Submodule dichiarati in ``.gitmodules`` → commit del gitlink nell'indice."""
    out = ctx.run(
        ["git", "config", "-f", ".gitmodules", "--get-regexp", r"submodule\..*\.path"],
        60,
        None,
        ctx.repo,
    )
    paths = [line.split(maxsplit=1)[1] for line in out.stdout.splitlines() if " " in line]
    links: dict[str, str] = {}
    for path in paths:
        ls = ctx.run(["git", "ls-files", "--stage", "--", path], 60, None, ctx.repo)
        fields = ls.stdout.split()
        is_gitlink = len(fields) >= _LS_FILES_MIN_FIELDS and fields[0] == _GITLINK_MODE
        links[path] = fields[1] if is_gitlink else ""
    return links


class SubmodulesCheck(DoctorCheck):
    """Commit e pulizia degli 8 submodule; stato del repository principale."""

    category = "submodules"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        head = ctx.run(["git", "rev-parse", "HEAD"], 60, None, ctx.repo)
        dirty = ctx.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], 60, None, ctx.repo
        )
        if head.returncode != 0:
            results.append(self.result("repository", Status.FAIL, "not a git repository"))
        else:
            from bench.store.manifest import dirty_paths

            changed = dirty_paths(dirty.stdout)
            state = f"dirty: {', '.join(changed[:5])}" if changed else "clean"
            results.append(
                self.result(
                    "repository",
                    Status.WARN if changed else Status.OK,
                    f"{head.stdout.strip()[:12]} ({state})",
                )
            )
        links = _gitlinks(ctx)
        count_status = Status.OK if len(links) == EXPECTED_SUBMODULES else Status.FAIL
        results.append(
            self.result(
                "count", count_status, f"{len(links)} declared, expected {EXPECTED_SUBMODULES}"
            )
        )
        for path, link in sorted(links.items()):
            sub = ctx.repo / path
            if not link:
                results.append(self.result(path, Status.FAIL, "no gitlink in the index"))
                continue
            if not (sub / ".git").exists():
                results.append(
                    self.result(
                        path, Status.FAIL, "not initialised: run scripts/setup_submodules.sh"
                    )
                )
                continue
            actual = ctx.run(
                ["git", "-C", str(sub), "rev-parse", "HEAD"], 60, None, ctx.repo
            ).stdout.strip()
            local = ctx.run(
                ["git", "-C", str(sub), "status", "--porcelain"], 60, None, ctx.repo
            ).stdout.strip()
            if actual != link:
                results.append(
                    self.result(path, Status.FAIL, f"checked out {actual[:12]}, pinned {link[:12]}")
                )
            elif local:
                results.append(
                    self.result(
                        path, Status.FAIL, "local changes (third_party/ must stay untouched)"
                    )
                )
            else:
                results.append(self.result(path, Status.OK, link[:12]))
        return results


class PatchedCopiesCheck(DoctorCheck):
    """Copie patchate in ``build/patched/`` aggiornate rispetto a submodule e patch."""

    category = "patched"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        links = _gitlinks(ctx)
        for name, method in sorted(ctx.cfg.methods_catalog.items()):
            copy_dir = ctx.repo / method.patched_dir
            commit_file = copy_dir.with_name(f"{name}.source_commit")
            sha_file = copy_dir.with_name(f"{name}.patches.sha256")
            if not copy_dir.is_dir() or not commit_file.is_file() or not sha_file.is_file():
                results.append(
                    self.result(name, Status.FAIL, "missing: run scripts/apply_patches.sh")
                )
                continue
            problems: list[str] = []
            if commit_file.read_text(encoding="utf-8").strip() != links.get(method.submodule, ""):
                problems.append("source commit differs from the submodule gitlink")
            recorded = {
                line.split(maxsplit=1)[1].lstrip("*").strip(): line.split(maxsplit=1)[0]
                for line in sha_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            current = {
                f"patches/{name}/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ctx.repo / "patches" / name).glob("[0-9][0-9][0-9][0-9]-*.patch"))
            }
            if recorded != current:
                problems.append("patches changed since the copy was built")
            if problems:
                results.append(
                    self.result(
                        name, Status.FAIL, "; ".join(problems) + ": rerun scripts/apply_patches.sh"
                    )
                )
            else:
                results.append(self.result(name, Status.OK, f"{len(current)} patch(es) applied"))
        return results


class ApptainerCheck(DoctorCheck):
    """Versione di Apptainer (cluster_info §5)."""

    category = "apptainer"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        proc = ctx.run(["apptainer", "--version"], 60, None, None)
        if proc.returncode != 0:
            version_result = self.result(
                "version", Status.FAIL, proc.stderr.strip() or "apptainer not available"
            )
        else:
            match = re.search(r"(\d+\.\d+\.\d+)", proc.stdout)
            found = match.group(1) if match else proc.stdout.strip()
            status = Status.OK if found == EXPECTED_APPTAINER else Status.FAIL
            version_result = self.result(
                "version", status, f"{found} (expected {EXPECTED_APPTAINER})"
            )
        return [version_result, *self._image(ctx, proc.returncode == 0)]

    def _image(self, ctx: DoctorContext, apptainer_ok: bool) -> list[CheckResult]:
        """Immagine della sandbox: ``.sif`` con hash, directory derivata, esecuzione senza rete."""
        execution = ctx.cfg.execution
        sif, image_dir = execution.image_sif, execution.image_dir
        sha_file = Path(f"{sif}.sha256")
        if not sif.is_file() or not sha_file.is_file():
            return [
                self.result(
                    "sandbox image", Status.FAIL, f"{sif} not built: run scripts/build_sandbox.sh"
                )
            ]
        recorded = sha_file.read_text(encoding="utf-8").split()[0]
        digest = hashlib.sha256()
        with open(sif, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        same = digest.hexdigest() == recorded
        results = [
            self.result(
                "sandbox image",
                Status.OK if same else Status.FAIL,
                f"sha256 {recorded[:12]}"
                if same
                else "sandbox.sif does not match sandbox.sif.sha256",
            )
        ]
        runner = image_dir / "opt" / "wmb" / "wmb_runner.py"
        if not runner.is_file():
            results.append(
                self.result("sandbox dir", Status.FAIL, f"{image_dir} missing: rebuild the image")
            )
            return results
        if not apptainer_ok:
            results.append(self.result("sandbox exec", Status.SKIP, "apptainer not available"))
            return results
        # La rete deve risultare assente (--network none, ADR-001).
        probe = (
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('pypi.org', 443), 5); print('network')\n"
            "except OSError:\n"
            "    print('no-network')\n"
        )
        cmd = ["apptainer", "exec", "--containall", "--cleanenv", "--no-home"]
        cmd += ["--no-mount", "bind-paths"]
        if execution.network_none:
            cmd += ["--net", "--network", "none"]
        proc = ctx.run([*cmd, str(image_dir), "python3", "-c", probe], 300, None, None)
        output = proc.stdout.strip()
        if proc.returncode != 0:
            status, detail = Status.FAIL, (proc.stderr.strip() or output)[-300:]
        elif output == "no-network":
            status, detail = Status.OK, "runs, network disabled"
        else:
            status, detail = Status.FAIL, f"unexpected output: {output[-200:]}"
        results.append(self.result("sandbox exec", status, detail))
        return results


class ModelsCheck(DoctorCheck):
    """Snapshot dei modelli nella cache HF condivisa (cluster_info §7)."""

    category = "models"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for model_id, spec in sorted(ctx.cfg.models_catalog.items()):
            if not spec.path.is_dir():
                results.append(
                    self.result(
                        model_id,
                        Status.FAIL,
                        f"snapshot not found: {_short(spec.path, ctx.cfg.paths.hf_hub_cache)}",
                    )
                )
            elif not (spec.path / "config.json").is_file():
                results.append(
                    self.result(model_id, Status.FAIL, "config.json missing in snapshot")
                )
            elif not spec.tokenizer_path.is_dir():
                results.append(
                    self.result(
                        model_id, Status.FAIL, f"tokenizer path not found: {spec.tokenizer_path}"
                    )
                )
            else:
                results.append(
                    self.result(model_id, Status.OK, f"{spec.hf_repo}@{spec.snapshot[:12]}")
                )
        return results


class DatasetsCheck(DoctorCheck):
    """File e cartelle dei dataset locali (cluster_info §8)."""

    category = "datasets"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for name, spec in sorted(ctx.cfg.datasets.items()):
            root = ctx.cfg.paths.datasets
            missing = [_short(p, root) for p in spec.files.values() if not p.is_file()]
            missing += [_short(p, root) for p in spec.dirs.values() if not p.is_dir()]
            total = len(spec.files) + len(spec.dirs)
            if not total:
                results.append(self.result(name, Status.SKIP, "no files declared"))
            elif missing:
                extra = len(missing) - _MAX_SHOWN
                shown = ", ".join(missing[:_MAX_SHOWN]) + (f" (+{extra} more)" if extra > 0 else "")
                results.append(
                    self.result(name, Status.FAIL, f"{len(missing)}/{total} missing: {shown}")
                )
            else:
                results.append(self.result(name, Status.OK, f"{total} path(s) present"))
        results.append(self.result("tests4py", Status.SKIP, "deferred to Milestone 13"))
        return results


class SourceryTokenCheck(DoctorCheck):
    """Presenza (mai il valore) di ``SOURCERY_TOKEN``, necessario ad ACW."""

    category = "secrets"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        present = bool(ctx.environ.get("SOURCERY_TOKEN"))
        if present:
            return [self.result("SOURCERY_TOKEN", Status.OK, "set")]
        return [self.result("SOURCERY_TOKEN", Status.WARN, "not set (required by the ACW worker)")]


class SlurmProfilesCheck(DoctorCheck):
    """Coerenza dei profili SLURM con partizioni, account, QoS e limiti (cluster_info §4)."""

    category = "slurm"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        policy = ResourcePolicy(ctx.cfg.resources, ctx.profiles, ctx.cfg.slurm)
        results = self._profiles(ctx)
        chosen, policy_results = self._policy(policy)
        return results + policy_results + self._timeouts(ctx, policy, chosen)

    def _profiles(self, ctx: DoctorContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for name, profile in sorted(ctx.profiles.items()):
            if profile.kind != "slurm":
                continue
            issues = validate_profile(profile, ctx.cfg.slurm)
            errors = [i.message for i in issues if i.severity == "error"]
            warnings = [i.message for i in issues if i.severity == "warning"]
            summary = (
                f"{profile.partition}, qos {profile.qos}, gres {profile.gres or '-'}, "
                f"{profile.timeout_min} min" + (" [provisional]" if profile.provisional else "")
            )
            if errors:
                results.append(self.result(name, Status.FAIL, "; ".join(errors)))
            elif warnings:
                results.append(self.result(name, Status.WARN, f"{summary}; " + "; ".join(warnings)))
            else:
                results.append(self.result(name, Status.OK, summary))
        return results

    def _policy(
        self, policy: ResourcePolicy
    ) -> tuple[dict[ResourceClass, ClusterProfile], list[CheckResult]]:
        chosen: dict[ResourceClass, ClusterProfile] = {}
        results: list[CheckResult] = []
        for resource in ResourceClass:
            try:
                chosen[resource] = policy.profile_for(resource)
                results.append(
                    self.result(f"policy {resource}", Status.OK, f"-> {chosen[resource].name}")
                )
            except (ConfigError, KeyError) as exc:
                results.append(self.result(f"policy {resource}", Status.FAIL, str(exc)))
        return chosen, results

    def _timeouts(
        self,
        ctx: DoctorContext,
        policy: ResourcePolicy,
        chosen: dict[ResourceClass, ClusterProfile],
    ) -> list[CheckResult]:
        results: list[CheckResult] = []
        for name, method in sorted(ctx.cfg.methods_catalog.items()):
            limits: list[str] = []
            for stage in ("watermark", "detect"):
                try:
                    resource = policy.resource_for(stage, method=name)
                except ConfigError as exc:
                    limits.append(str(exc))
                    continue
                target = chosen.get(resource)
                if target is None or target.timeout_min is None:
                    continue
                if method.worker_timeout_s >= target.timeout_min * 60:
                    limits.append(
                        f"{stage}: worker_timeout_s {method.worker_timeout_s} >= "
                        f"{target.name} timeout {target.timeout_min * 60}s"
                    )
            if limits:
                results.append(self.result(f"timeout {name}", Status.FAIL, "; ".join(limits)))
            else:
                detail = f"worker_timeout_s {method.worker_timeout_s}"
                results.append(self.result(f"timeout {name}", Status.OK, detail))
        return results


class PathsCheck(DoctorCheck):
    """Radici scrivibili per artefatti e file temporanei."""

    category = "paths"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for label, path in (("artifacts", ctx.cfg.paths.artifacts), ("tmp", ctx.cfg.paths.tmp)):
            try:
                path.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=path, prefix=".doctor-", delete=True):
                    pass
                results.append(self.result(label, Status.OK, f"{path} (writable)"))
            except OSError as exc:
                results.append(self.result(label, Status.FAIL, f"{path}: {exc}"))
        return results


def gpu_envs(cfg: ExperimentConfig) -> set[str]:
    """Ambienti che richiedono CUDA: bench-core e i metodi con almeno una fase su GPU.

    Le fasi per metodo vengono da ``resources.gpu_methods_by_stage``: un metodo solo CPU
    (es. ACW) può non avere torch nel proprio ambiente.
    """
    envs = {"bench-core"}
    for by_method in cfg.resources.gpu_methods_by_stage.values():
        for method, uses_gpu in by_method.items():
            if uses_gpu and method in cfg.methods_catalog:
                envs.add(cfg.methods_catalog[method].env)
    return envs


class GpuCheck(DoctorCheck):
    """CUDA negli ambienti che la richiedono (solo con ``--gpu``, su un nodo NVIDIA)."""

    category = "gpu"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        if not ctx.gpu:
            return [self.result("cuda", Status.SKIP, "use 'bench doctor --gpu' on a GPU node")]
        results: list[CheckResult] = []
        required = gpu_envs(ctx.cfg)
        for name, spec in sorted(ctx.cfg.envs.items()):
            if name not in required:
                results.append(
                    self.result(name, Status.SKIP, "CPU-only method (resources): CUDA not required")
                )
                continue
            if not spec.python.exists():
                results.append(self.result(name, Status.FAIL, "interpreter not found"))
                continue
            proc = ctx.run(
                [str(spec.python), "-c", _GPU_PROBE],
                GPU_PROBE_TIMEOUT_S,
                ctx.clean_env(),
                Path("/"),
            )
            info = _parse_json_line(proc.stdout) if proc.returncode == 0 else None
            if info is None:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
                results.append(self.result(name, Status.FAIL, f"torch probe failed: {tail[0]}"))
            elif not info.get("cuda"):
                results.append(
                    self.result(
                        name, Status.FAIL, f"CUDA not available (torch {info.get('torch')})"
                    )
                )
            else:
                results.append(
                    self.result(
                        name,
                        Status.OK,
                        f"torch {info.get('torch')}, CUDA {info.get('cuda_version')}, "
                        f"{info.get('devices')} device(s)",
                    )
                )
        return results


class AttackToolsCheck(DoctorCheck):
    """Versioni degli strumenti degli attacchi (installati in Milestone 10)."""

    category = "attack tools"

    def run(self, ctx: DoctorContext) -> list[CheckResult]:
        return [self.result("tools", Status.SKIP, "installed in Milestone 10")]


DEFAULT_CHECKS: tuple[type[DoctorCheck], ...] = (
    EnvironmentsCheck,
    SubmodulesCheck,
    PatchedCopiesCheck,
    ApptainerCheck,
    ModelsCheck,
    DatasetsCheck,
    SourceryTokenCheck,
    SlurmProfilesCheck,
    PathsCheck,
    GpuCheck,
    AttackToolsCheck,
)


# ----------------------------------------------------------------------------- report


@dataclass
class DoctorReport:
    """Risultati del doctor."""

    results: list[CheckResult]

    def count(self, status: Status) -> int:
        """Numero di risultati con lo stato dato."""
        return sum(r.status is status for r in self.results)

    @property
    def exit_code(self) -> int:
        """1 se almeno un controllo è FAIL, altrimenti 0."""
        return 1 if self.count(Status.FAIL) else 0

    def render(self) -> str:
        """Report testuale raggruppato per categoria."""
        width = max((len(r.name) for r in self.results), default=10)
        lines = ["bench doctor", "=" * 12]
        category = None
        for r in self.results:
            if r.category != category:
                category = r.category
                lines.append("")
                lines.append(f"[{category}]")
            lines.append(f"  {r.status.value:<4}  {r.name:<{width}}  {r.detail}")
        lines.append("")
        lines.append(
            "summary: "
            + ", ".join(f"{self.count(s)} {s.value}" for s in Status)
            + (" -> FAILED" if self.exit_code else " -> OK")
        )
        return "\n".join(lines) + "\n"

    def to_json(self) -> str:
        """Report in JSON."""
        return json.dumps(
            {"exit_code": self.exit_code, "results": [asdict(r) for r in self.results]},
            indent=2,
            ensure_ascii=False,
        )


class Doctor:
    """Esegue i controlli e raccoglie il report.

    Args:
        ctx: contesto dei controlli.
        checks: classi di controllo da eseguire (default: tutte).
    """

    def __init__(
        self, ctx: DoctorContext, checks: Sequence[type[DoctorCheck]] = DEFAULT_CHECKS
    ) -> None:
        self.ctx = ctx
        self.checks = list(checks)

    def run(self) -> DoctorReport:
        """Esegue tutti i controlli; un controllo che solleva diventa un FAIL."""
        results: list[CheckResult] = []
        for check_cls in self.checks:
            check = check_cls()
            try:
                results.extend(check.run(self.ctx))
            except Exception as exc:  # un controllo rotto non deve fermare il report
                results.append(
                    CheckResult(check.category, "<check>", Status.FAIL, f"check crashed: {exc!r}")
                )
        return DoctorReport(results)
