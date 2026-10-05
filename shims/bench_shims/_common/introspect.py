"""Versioni dell'ambiente del worker per il manifest (SPEC §8.3). Compatibile con Python 3.9.

La cartella della copia patchata arriva in ``WMB_METHOD_SOURCE`` (impostata dal
``WorkerClient``): accanto ci sono ``<metodo>.source_commit`` e ``<metodo>.patches.sha256``
scritti da ``scripts/apply_patches.sh``.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from typing import Any, Dict


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def introspect(method: str) -> Dict[str, Any]:
    """Python, pacchetti (``pip freeze`` e suo hash), torch e CUDA, commit e patch del metodo."""
    info: Dict[str, Any] = {"method": method, "python": platform.python_version()}
    try:
        freeze = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        freeze = f"unavailable: {exc}"
    packages = sorted(line for line in freeze.splitlines() if line.strip())
    info["packages"] = packages
    info["packages_sha256"] = hashlib.sha256("\n".join(packages).encode("utf-8")).hexdigest()
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = torch.version.cuda
        info["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except ImportError:
        info["torch"] = None
    try:
        import transformers

        info["transformers"] = transformers.__version__
    except ImportError:
        info["transformers"] = None
    source = os.environ.get("WMB_METHOD_SOURCE", "")
    if source:
        base = source.rstrip("/\\")
        info["source_commit"] = _read(base + ".source_commit")
        info["patches_sha256"] = _read(base + ".patches.sha256")
    return info
