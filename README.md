# wm-bench

Framework unificato e riproducibile di benchmark per cinque metodi di watermarking training-free
di codice generato da LLM (SWEET, ACW, STONE, PromptMark, MCGMark).

- Specifica vincolante: [`SPEC.md`](SPEC.md)
- Informazioni sul cluster e decisioni dell'utente (prevalgono su SPEC): [`docs/cluster_info.md`](docs/cluster_info.md)
- Deviazioni dal protocollo: [`docs/deviations.md`](docs/deviations.md)
- Decisioni architetturali: [`docs/decisions/`](docs/decisions/)
- Log di chiusura delle milestone (eseguiti sul cluster): [`docs/milestone_logs/`](docs/milestone_logs/)

## Struttura

| Percorso | Contenuto |
|---|---|
| `packages/bench-contracts/` | contratti stdlib-only tra orchestratore e worker, installati in tutti e 6 gli ambienti |
| `src/bench/` | orchestratore (ambiente `bench-core`, Python 3.11): dominio, store, configurazione, pipeline, doctor, CLI |
| `configs/` | configurazione Hydra (percorsi, ambienti, profili SLURM, modelli, metodi, dataset) |
| `third_party/` | repository originali dei metodi e dipendenze, come submodule fissati (ADR-002) |
| `patches/<metodo>/` | modifiche al codice dei metodi, come patch numerate con README |
| `build/patched/<metodo>/` | copie patchate eseguite dai worker (non versionate) |
| `configs/envs/envs.yaml` | interpreti Python dei 6 ambienti |
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

## CLI `bench` (Milestone 1)

Dopo `python -m pip install --no-deps --no-build-isolation -e .` nell'ambiente `bench-core`
(`--no-build-isolation` usa il setuptools già presente nell'ambiente, così l'installazione non
dipende dalla connessione a PyPI):

```bash
bench doctor                      # diagnostica dal nodo di login (exit code 1 se c'è un FAIL)
bench doctor --gpu                # anche CUDA negli ambienti che la usano: solo su un nodo NVIDIA (gpuq/aiq)
bench doctor --json report.json   # report anche in JSON
bench stage=selftest              # fase di autoverifica: scrive, poi SKIPPED; force=true per rieseguire
bench stage=selftest paths=local  # prove locali sotto $WMB_LOCAL_ROOT (default: <repo>/.local_wmb)
bench stage=prepare_data levels=[L1]  # problemi, divisione e negativi di L1 (job CPU su defq, ADR-004)
bench submit --dry-run stage=generate_baseline levels=[L1] splits=[dev,test]  # mostra il job e le celle
bench submit stage=generate_baseline levels=[L1] splits=[dev,test]  # baseline: UN job gpuq, celle in sequenza (ADR-006)
```

Gli artefatti stanno in `$WMB/artifacts` (fuori dal repository), con un manifest accanto a ogni file
(ADR-003). Log per fase e cella in `artifacts/_logs/`, tempi in `artifacts/_timing/timings.jsonl`.
