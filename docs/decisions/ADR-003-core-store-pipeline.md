# ADR-003 — Nucleo: identificatori, store, pipeline, configurazione e doctor

- **Stato:** accettata (Milestone 1, 2 ottobre 2026)
- **Contesto:** SPEC §5.3, §7.1, §7.10, §7.11, §15, §16, §17; `docs/cluster_info.md` §1–§8, §10.

## Decisioni

1. **`config_hash`** = i primi 12 caratteri esadecimali dello **SHA-256** del JSON canonico
   (`sort_keys=True`, separatori compatti, UTF-8, NaN vietati) di
   `{"hparams", "contract_version", "submodule_commit"}`. SPEC fissa il contenuto e la lunghezza, ma non
   l'algoritmo. **`sample_id`** usa xxh3_128 come richiesto da SPEC, con i campi assenti come stringa vuota.

2. **Validità e atomicità degli artefatti** (`ArtifactStore`). Ordine di scrittura:
   1. si cancella il manifest precedente;
   2. i dati vanno in un file temporaneo nella stessa cartella, con `fsync` e `os.replace`;
   3. il manifest va scritto allo stesso modo, con `status=complete`, SHA-256 e dimensione dei dati.

   - `exists()` richiede file presente, manifest `complete` e dimensione coincidente: è un controllo
     economico, adatto a decidere SKIPPED. `verify()` ricalcola anche lo SHA-256.
   - Un crash in qualunque punto non lascia mai un artefatto "complete" incoerente.
   - Ogni manifest registra: `n_rows_in`, `n_rows_out`, `n_rows_expected`, gli hash degli input (lineage)
     e la provenienza (commit di repository e submodule, hash delle patch, pacchetti del core, nodo e job
     SLURM, GPU).

3. **Invariante I1.** Ogni fase dichiara nel manifest `n_rows_out` e `n_rows_expected`.
   L'`InvariantObserver` li confronta e, per i Parquet, controlla anche il numero reale di righe dai
   metadati. In caso di discrepanza solleva `InvariantViolation` (fatale).

4. **Fasi e celle.** Ogni fase dichiara `output_kinds` (per indicare, in `MissingInputError`, quale fase
   lanciare) e `cell_axes`. Il `CellPlanner` costruisce le celle come prodotto cartesiano di quegli assi
   sulle liste della configurazione. La pipeline è fail-fast sugli errori di fase: i fallimenti dei
   singoli campioni restano stati (I1), non eccezioni.

5. **Registri incrementali.** In M1 esiste solo `STAGES`. `LOADERS`, `EXECUTORS`, `METHODS`, `METRICS`
   e `ATTACKS` si aggiungono insieme alle rispettive classi base (M2, M4, M5, M7, M10), così non ci sono
   registri senza tipo né stub.

6. **Configurazione.**
   - Le cartelle `model/`, `method/` e `dataset/` sono cataloghi composti nella defaults list
     (`model@models_catalog.<id>`, …).
   - `ExperimentConfig` (Pydantic, immutabile) fa i controlli incrociati: metodi tra i 5 noti, ambienti
     esistenti, modelli presenti nel catalogo, path del modello uguale alla snapshot.
   - **`global_seed` bloccato a 20261001** (decisione dell'utente): ogni altro valore è un `ConfigError`.
   - **Artefatti fuori dal repository** (decisione dell'utente): `paths.artifacts = $WMB/artifacts` e
     `paths.tmp = $WMB/tmp`. Hydra scrive la propria cartella di run in `${paths.artifacts}/_hydra`
     (`job.chdir=false`).
   - `paths=local` serve per le prove locali, sotto `$WMB_LOCAL_ROOT`.

7. **Risorse e profili SLURM** (cluster_info §4).
   - Le regole della `ResourcePolicy` stanno in `configs/resources/default.yaml`:
     - GPU per `generate_baseline` e `imperceptibility`;
     - `watermark` su GPU tranne ACW;
     - `detect` su GPU solo per SWEET (gli altri metodi in attesa degli audit);
     - attacchi T2 e T3 su GPU, T1 e T4 su CPU.
   - `validate_profile` rifiuta: GPU su partizioni non NVIDIA, partizioni non utilizzabili (fatq, thinq),
     account diverso da `did_tesi_nlp_330`, QoS diversa da quella della partizione, `timeout_min` pari o
     superiore al limite. Avvisa sopra l'80% del limite.
   - Profili:
     - `slurm_gpu`: gpuq, 330 min;
     - `slurm_gpu_h100`: aiq, 330 min;
     - `slurm_cpu`: defq senza GPU, 16 CPU, 64 GB, 420 min, **provvisorio** fino alla M4.
   - `worker_timeout_s` vale 18000 s (5 h), sotto i 7 h delle partizioni GPU.

8. **`bench doctor`.**
   - I controlli sono strategie indipendenti; un controllo che solleva diventa un FAIL e non interrompe
     il report.
   - I controlli su CUDA girano solo con `--gpu`, perché di norma il doctor si lancia dal nodo di login,
     e solo negli ambienti che la usano: bench-core e i metodi con almeno una fase su GPU secondo
     `resources.gpu_methods_by_stage`. Gli ambienti solo CPU (oggi `acw`, che non ha torch) sono SKIP.
   - `SOURCERY_TOKEN`: se ne verifica solo la presenza, il valore non compare mai nel report né nel JSON.
   - Exit code 1 se c'è almeno un FAIL.
   - Valori attesi presi da cluster_info: 8 submodule, Apptainer 1.1.9, Python 3.11 per bench-core e
     ≥ 3.9 per gli altri ambienti.

## Conseguenze

- Cambiare `config_hash` o il formato del manifest invalida gli artefatti esistenti: va fatto solo con
  un nuovo ADR.
- Le fasi reali (dalla M2) devono scrivere tramite `StageContext.make_manifest` e dichiarare
  `n_rows_expected`.
