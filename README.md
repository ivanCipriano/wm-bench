# wm-bench

Framework unificato e riproducibile di benchmark per sei metodi di watermarking training-free
di codice generato da LLM (SWEET, ACW, STONE, Code Acrostic, PromptMark, MCGMark).

- Specifica vincolante: [`SPEC.md`](SPEC.md)
- Informazioni sul cluster e decisioni dell'utente (prevalgono su SPEC): [`docs/cluster_info.md`](docs/cluster_info.md)
- Deviazioni dal protocollo: [`docs/deviations.md`](docs/deviations.md)
- Decisioni architetturali: [`docs/decisions/`](docs/decisions/)

## Struttura (Milestone 0)

| Percorso | Contenuto |
|---|---|
| `packages/bench-contracts/` | contratti stdlib-only tra orchestratore e worker, installati in tutti e 7 gli ambienti |
| `src/bench/` | orchestratore (ambiente `bench-core`, Python 3.11) |
| `third_party/` | repository originali dei metodi e dipendenze, come submodule fissati (ADR-002) |
| `patches/<metodo>/` | modifiche al codice dei metodi, come patch numerate con README |
| `build/patched/<metodo>/` | copie patchate eseguite dai worker (non versionate) |
| `configs/envs/envs.yaml` | interpreti Python dei 7 ambienti |
| `scripts/` | `setup_submodules.sh`, `apply_patches.sh`, `install_contracts.sh` |
| `tests/` | `unit/`, `contract/` (marker `contract`), poi `oracle/`, `property/`, `integration/` |

## Avvio sul cluster

```bash
export REPO=/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench
cd "$REPO"
bash scripts/setup_submodules.sh
bash scripts/apply_patches.sh
bash scripts/install_contracts.sh
WMB_REQUIRE_ENVS=1 /mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/bench-core/bin/python -m pytest tests/contract -m contract
```
