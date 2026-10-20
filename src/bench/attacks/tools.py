"""Esecuzione dei tool esterni degli attacchi T1 a lotti (SPEC §7.7, §12, §20.2; ADR-012).

Un avvio della JVM o di Maven per campione costerebbe ore: il ``ToolRunner`` scrive i campioni
di un lotto in una cartella temporanea, lancia il tool **una volta** sul lotto e rilegge i file.
Se il tool fallisce sul lotto, il lotto si divide a metà finché il fallimento resta isolato su
singoli file (``TOOL_ERROR`` per quei campioni, con il codice originale). Ogni invocazione ha un
timeout.

I tool si cercano nell'ambiente di ``bench-core`` (la cartella dell'interprete va in testa al
``PATH``) e in ``tools/`` del repository (``node_modules``, jar, ``m2``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)

GJF_VERSION = "1.24.0"
# google-java-format su JDK >= 16 richiede questi export (README del progetto).
GJF_JVM_FLAGS = tuple(
    f"--add-exports=jdk.compiler/com.sun.tools.javac.{pkg}=ALL-UNNAMED"
    for pkg in ("api", "code", "file", "parser", "tree", "util")
)
_JAVA_PACKAGE = re.compile(r"^\s*package\s+[\w.]+\s*;[ \t]*\n?", re.MULTILINE)
_JAVA_PUBLIC_TYPE = re.compile(
    r"\bpublic\s+(?:(?:final|abstract|static|sealed|non-sealed|strictfp)\s+)*"
    r"(?:class|interface|enum|record|@interface)\s+([A-Za-z_]\w*)"
)
_OUR_PACKAGE = re.compile(r"\Apackage p\d+;\n")
# Diagnosi d'errore di clang/clang-tidy (su stdout; su stderr c'è solo il riepilogo).
_DIAGNOSTIC_ERROR = re.compile(r"^\S+:\d+:\d+: (?:fatal )?error: .*$", re.MULTILINE)
MAX_ERROR_LINES = 5


def _error_text(proc: subprocess.CompletedProcess[str]) -> str:
    """Messaggio di un'invocazione fallita: fine di stderr (o stdout) più le prime diagnosi
    ``error:`` di stdout, che altrimenti andrebbero perse (clang-tidy)."""
    tail = (proc.stderr or proc.stdout or "")[-1500:]
    errors = _DIAGNOSTIC_ERROR.findall(proc.stdout or "")[:MAX_ERROR_LINES] if proc.stderr else []
    head = "\n".join(errors)
    return f"exit {proc.returncode}: {head + chr(10) if head else ''}{tail}"


@dataclass(frozen=True)
class ToolResult:
    """Esito del tool su un file: codice nuovo oppure errore."""

    code: str | None
    error: str | None = None


@dataclass(frozen=True)
class _Layout:
    """Come scrivere e rileggere i file di un lotto."""

    write: Callable[[Path, int, str], Path]
    read: Callable[[Path, str], str]


def _flat(suffix: str) -> _Layout:
    def write(root: Path, i: int, code: str) -> Path:
        path = root / f"f{i:05d}{suffix}"
        path.write_text(code, encoding="utf-8", newline="")
        return path

    return _Layout(write, lambda path, _orig: path.read_text(encoding="utf-8"))


def _java_type_name(code: str) -> str:
    match = _JAVA_PUBLIC_TYPE.search(code)
    return match.group(1) if match else "Solution"


def _maven_layout() -> _Layout:
    """Progetto Maven: un package per file (``p00001``), così classi con lo stesso nome
    convivono; la dichiarazione ``package`` aggiunta si toglie in lettura e quella originale
    (se c'era) si rimette."""

    def write(root: Path, i: int, code: str) -> Path:
        pkg = f"p{i:05d}"
        folder = root / "src" / "main" / "java" / pkg
        folder.mkdir(parents=True, exist_ok=True)
        body = _JAVA_PACKAGE.sub("", code, count=1)
        path = folder / f"{_java_type_name(code)}.java"
        path.write_text(f"package {pkg};\n{body}", encoding="utf-8", newline="")
        return path

    def read(path: Path, original: str) -> str:
        text = path.read_text(encoding="utf-8")
        text = _OUR_PACKAGE.sub("", text, count=1)
        if text.startswith("package p"):  # il tool ha riformattato la riga
            text = _JAVA_PACKAGE.sub("", text, count=1)
        old = _JAVA_PACKAGE.search(original)
        if old is not None:
            text = old.group(0) + text
        return text

    return _Layout(write, read)


class ToolRunner:
    """Esecutore dei tool degli attacchi.

    Args:
        tools_root: cartella ``tools/`` del repository.
        tmp_root: cartella per i file temporanei dei lotti.
        chunk_size: file per invocazione (Maven ne usa di più: l'avvio costa).
        timeout_s: timeout di base di un'invocazione (più ``per_file_s`` per file).
    """

    def __init__(
        self,
        tools_root: Path,
        tmp_root: Path,
        chunk_size: int = 200,
        timeout_s: float = 120.0,
        per_file_s: float = 2.0,
    ) -> None:
        self.tools_root = Path(tools_root)
        self.tmp_root = Path(tmp_root)
        self.chunk_size = chunk_size
        self.timeout_s = timeout_s
        self.per_file_s = per_file_s
        self._versions: dict[str, str] = {}
        self._includes: list[str] | None = None

    # ------------------------------------------------------------------ ambiente
    def env(self) -> dict[str, str]:
        """Ambiente dei sottoprocessi: la cartella di ``bench-core`` in testa al ``PATH``."""
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
        return env

    def which(self, name: str) -> str | None:
        """Percorso di un eseguibile (``None`` se manca)."""
        return shutil.which(name, path=self.env()["PATH"])

    def prelude(self, text: str, suffix: str = ".h") -> Path:
        """File di contesto da includere prima del sorgente (es. ``clang -include``): stesso
        contenuto, stesso file, scritto una volta in ``tmp_root``."""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        path = self.tmp_root / f"prelude_{digest}{suffix}"
        if not path.is_file():
            self.tmp_root.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)  # atomico: job paralleli scrivono lo stesso contenuto
        return path

    @property
    def js_bin(self) -> Path:
        return self.tools_root / "js" / "node_modules" / ".bin"

    @property
    def gjf_jar(self) -> Path:
        return self.tools_root / "java" / f"google-java-format-{GJF_VERSION}-all-deps.jar"

    @property
    def m2(self) -> Path:
        return self.tools_root / "java" / "m2"

    def available(self, tool: str) -> bool:
        """Se il tool è installato (per i test, che altrimenti si saltano)."""
        checks: dict[str, Callable[[], bool]] = {
            "ruff": lambda: True,
            "google-java-format": lambda: self.which("java") is not None and self.gjf_jar.is_file(),
            "openrewrite": lambda: self.which("mvn") is not None and self.m2.is_dir(),
            "clang-format": lambda: self.which("clang-format") is not None,
            "clang-tidy": lambda: self.which("clang-tidy") is not None,
            "prettier": lambda: (self.js_bin / "prettier").exists(),
            "eslint": lambda: (self.js_bin / "eslint").exists(),
        }
        return checks[tool]()

    def version(self, tool: str) -> str:
        """Versione effettiva del tool (in cache), per il manifest."""
        if tool not in self._versions:
            cmd = {
                "ruff": [sys.executable, "-m", "ruff", "--version"],
                "google-java-format": [
                    "java", *GJF_JVM_FLAGS, "-jar", str(self.gjf_jar), "--version",
                ],
                "openrewrite": ["mvn", "-v"],
                "clang-format": ["clang-format", "--version"],
                "clang-tidy": ["clang-tidy", "--version"],
                "prettier": [str(self.js_bin / "prettier"), "--version"],
                "eslint": [str(self.js_bin / "eslint"), "--version"],
            }[tool]  # fmt: skip
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60, env=self.env(), check=False
                )
                text = (proc.stdout or proc.stderr).strip().splitlines()
                self._versions[tool] = text[0] if text else "?"
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._versions[tool] = f"unavailable ({type(exc).__name__})"
            if tool == "openrewrite":
                self._versions[tool] += (
                    "; rewrite-maven-plugin 5.45.0; rewrite-static-analysis 1.20.0"
                )
        return self._versions[tool]

    # ------------------------------------------------------------------ comandi dei tool
    def run(
        self, tool: str, codes: Sequence[str], extra_args: Sequence[str] = ()
    ) -> list[ToolResult]:
        """Applica un tool a una lista di sorgenti, a lotti (``extra_args`` in coda al comando,
        es. ``-Dwmb.recipe=...`` per una sola ricetta di OpenRewrite nella diagnosi)."""
        spec = self._spec(tool)
        if extra_args:
            base = spec.command
            spec = replace(spec, command=lambda root, files: [*base(root, files), *extra_args])
        out: list[ToolResult] = []
        size = spec.chunk or self.chunk_size
        for start in range(0, len(codes), size):
            out.extend(self._run_chunk(spec, list(codes[start : start + size])))
        return out

    def _spec(self, tool: str) -> _ToolSpec:
        builders: dict[str, Callable[[], _ToolSpec]] = {
            "ruff": self._ruff,
            "google-java-format": self._gjf,
            "openrewrite": self._openrewrite,
            "clang-format": self._clang_format,
            "clang-tidy": self._clang_tidy,
            "prettier": self._prettier,
            "eslint": self._eslint,
        }
        if tool not in builders:
            raise ValueError(f"unknown tool {tool!r}")
        return builders[tool]()

    def _ruff(self) -> _ToolSpec:
        return _ToolSpec(
            "ruff",
            _flat(".py"),
            lambda root, files: [
                sys.executable, "-m", "ruff", "check", "--fix", "--isolated", "--no-cache",
                "--quiet", *(f.name for f in files),
            ],
            ok_codes=(0, 1),  # 1 = restano violazioni non correggibili
        )  # fmt: skip

    def _gjf(self) -> _ToolSpec:
        return _ToolSpec(
            "google-java-format",
            _flat(".java"),
            lambda root, files: [
                "java", *GJF_JVM_FLAGS, "-jar", str(self.gjf_jar), "--replace",
                *(f.name for f in files),
            ],
        )  # fmt: skip

    def _openrewrite(self) -> _ToolSpec:
        pom = self.tools_root / "java" / "rewrite" / "pom.xml"
        return _ToolSpec(
            "openrewrite",
            _maven_layout(),
            lambda root, files: [
                "mvn", "-B", "-q", "-o", f"-Dmaven.repo.local={self.m2}", "rewrite:run",
            ],
            prepare=lambda root: shutil.copy(pom, root),
            chunk=1000,
            timeout_s=900.0,
        )  # fmt: skip

    def _clang_format(self) -> _ToolSpec:
        style = self.tools_root / "cpp" / ".clang-format"
        return _ToolSpec(
            "clang-format",
            _flat(".cpp"),
            lambda root, files: [
                "clang-format", "-i", f"--style=file:{style}", *(f.name for f in files),
            ],
        )  # fmt: skip

    def compiler_includes(self) -> list[str]:
        """Header interni del compilatore (``stddef.h``…) per clang-tidy.

        clang-tidy di conda non ha la cartella delle risorse di clang: senza, ogni file che
        include la libreria standard dà ``'stddef.h' file not found``. Si usa quella del
        compilatore C++ di ``bench-core`` (``c++ -print-file-name=include``, il gcc di conda).
        """
        if self._includes is None:
            self._includes = []
            try:
                proc = subprocess.run(
                    ["c++", "-print-file-name=include"],
                    capture_output=True, text=True, timeout=60, env=self.env(), check=False,
                )  # fmt: skip
                path = proc.stdout.strip()
                if proc.returncode == 0 and path and Path(path).is_dir():
                    self._includes = ["-isystem", path]
            except (OSError, subprocess.TimeoutExpired):
                pass
        return self._includes

    def _clang_tidy(self) -> _ToolSpec:
        config = self.tools_root / "cpp" / ".clang-tidy"
        includes = self.compiler_includes()
        return _ToolSpec(
            "clang-tidy",
            _flat(".cpp"),
            lambda root, files: [
                "clang-tidy", "--quiet", "--fix", f"--config-file={config}",
                *(f.name for f in files), "--", "-std=c++17", *includes,
            ],
            per_file_s=10.0,
        )  # fmt: skip

    def _prettier(self) -> _ToolSpec:
        config = self.tools_root / "js" / ".prettierrc.json"
        return _ToolSpec(
            "prettier",
            _flat(".js"),
            lambda root, files: [
                str(self.js_bin / "prettier"), "--write", "--log-level", "warn",
                "--no-editorconfig", "--config", str(config), *(f.name for f in files),
            ],
        )  # fmt: skip

    def _eslint(self) -> _ToolSpec:
        config = self.tools_root / "js" / "eslint.config.mjs"
        return _ToolSpec(
            "eslint",
            _flat(".js"),
            lambda root, files: [
                str(self.js_bin / "eslint"), "--fix", "--no-warn-ignored", "-c", str(config),
                *(f.name for f in files),
            ],
            ok_codes=(0, 1),  # 1 = restano problemi non correggibili
        )  # fmt: skip

    # ------------------------------------------------------------------ lotti
    def _run_chunk(self, spec: _ToolSpec, codes: list[str]) -> list[ToolResult]:
        """Un'invocazione sul lotto; se fallisce, il lotto si divide a metà."""
        if not codes:
            return []
        error = None
        results: list[ToolResult] | None = None
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{spec.name}_", dir=self.tmp_root) as tmp:
            root = Path(tmp)
            if spec.prepare is not None:
                spec.prepare(root)
            files = [spec.layout.write(root, i, code) for i, code in enumerate(codes)]
            timeout = (spec.timeout_s or self.timeout_s) + (
                spec.per_file_s or self.per_file_s
            ) * len(codes)
            try:
                proc = subprocess.run(
                    spec.command(root, files),
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=self.env(),
                    check=False,
                )
                if proc.returncode in spec.ok_codes:
                    results = [
                        ToolResult(spec.layout.read(f, c))
                        for f, c in zip(files, codes, strict=True)
                    ]
                else:
                    error = _error_text(proc)
            except subprocess.TimeoutExpired:
                error = f"timeout after {timeout:.0f}s"
            except OSError as exc:
                error = f"{type(exc).__name__}: {exc}"
        if results is not None:
            return results
        if len(codes) == 1:
            return [ToolResult(None, error)]
        logger.info(
            "%s failed on a batch of %d files (%s): splitting", spec.name, len(codes), error
        )
        half = len(codes) // 2
        return self._run_chunk(spec, codes[:half]) + self._run_chunk(spec, codes[half:])


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    layout: _Layout
    command: Callable[[Path, list[Path]], list[str]]
    ok_codes: tuple[int, ...] = (0,)
    prepare: Callable[[Path], object] | None = None
    chunk: int | None = None
    timeout_s: float | None = None
    per_file_s: float | None = None
