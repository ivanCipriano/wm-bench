"""Test di contratto sugli ambienti (SPEC §21.2, criterio di accettazione M0).

Per ognuno dei 7 interpreti di ``configs/envs/envs.yaml``:
- ``import bench_contracts`` termina con exit code 0;
- la versione di Python è >= 3.9 (cluster_info §1);
- ``derive_seed`` dà gli stessi valori attesi (stabilità tra versioni, SPEC §21.3).

Un interprete inesistente fa saltare il test, a meno che ``WMB_REQUIRE_ENVS=1``
(da impostare sul cluster), nel qual caso il test fallisce.
I test ``--introspect`` degli shim si aggiungono in Milestone 5.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import REPO_ROOT, SEED_VECTORS

pytestmark = pytest.mark.contract

EXPECTED_ENVS = {"bench-core", "sweet", "acw", "stone", "code_acrostic", "promptmark", "mcgmark"}

_PROBE = r"""
import json, sys
import bench_contracts
from bench_contracts import derive_seed
vectors = json.loads(sys.argv[1])
print(json.dumps({
    "python": list(sys.version_info[:3]),
    "contracts_version": bench_contracts.__version__,
    "contracts_file": bench_contracts.__file__,
    "seeds": [derive_seed(*parts) for parts in vectors],
}))
"""


def _load_envs() -> dict[str, str]:
    with open(REPO_ROOT / "configs" / "envs" / "envs.yaml", encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle)
    return {name: str(spec["python"]) for name, spec in data["envs"].items()}


ENVS = _load_envs()


def test_envs_yaml_lists_all_seven_envs() -> None:
    assert set(ENVS) == EXPECTED_ENVS


@pytest.mark.parametrize("env_name", sorted(ENVS))
def test_env_imports_contracts(env_name: str) -> None:
    python = ENVS[env_name]
    if not Path(python).exists():
        if os.environ.get("WMB_REQUIRE_ENVS") == "1":
            pytest.fail(f"{env_name}: interpreter not found: {python}")
        pytest.skip(f"{env_name}: interpreter not found: {python}")

    vectors = [parts for parts, _ in SEED_VECTORS]
    # Ambiente pulito: niente PYTHONPATH ereditato, così si verifica l'installazione reale.
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    proc = subprocess.run(
        [python, "-c", _PROBE, json.dumps(vectors)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd="/",
        check=False,
    )
    assert proc.returncode == 0, f"{env_name}: exit {proc.returncode}\n{proc.stderr[-2000:]}"
    info = json.loads(proc.stdout.strip().splitlines()[-1])

    assert tuple(info["python"]) >= (3, 9), f"{env_name}: Python {info['python']}"
    assert info["seeds"] == [expected for _, expected in SEED_VECTORS]
