# Informazioni cluster e decisioni utente

> Documento di riferimento per l'agente, complementare a `SPEC.md`.
> Dove questo file contraddice `SPEC.md`, **prevale questo file** (vedi §1).
> Le voci marcate `DA COMPLETARE` vanno chiuse dall'utente; l'agente non deve inventarle.
> Nessun segreto (token, password) va scritto in questo file.

---

## 1. Differenze rispetto a SPEC.md (prevalgono su SPEC.md)

| Argomento | SPEC.md | Valore reale | Conseguenza per l'implementazione |
|---|---|---|---|
| Posizione del repository sul cluster | non specificata | `/mnt/beegfs/did_tesi_nlp_330/icipriano/repo_wm_bench` (**fuori** da `wm_bench/`) | Tutti i percorsi relativi del repository partono da qui |
| Nomi degli ambienti dei metodi | `env-sweet`, `env-acw`, … | `sweet`, `acw`, `stone`, `code_acrostic`, `promptmark`, `mcgmark` | Usare questi nomi in `configs/envs/envs.yaml` e nel campo `env` di `configs/method/*.yaml` |
| Ambiente orchestratore | `bench-core` in `miniforge3/envs/` | ambiente **a prefisso** in `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/bench-core` | Interprete: `<prefisso>/bin/python` |
| Versione minima di Python nei worker | ≥ 3.8 | ≥ **3.9** (minimo reale: `stone` = 3.9.19) | `bench-contracts` e shim compatibili con 3.9; resta vietata la sintassi `X | Y` nei tipi valutati a runtime e `match` |
| Codice eseguito dai worker (§8.1, §9.4 di SPEC) | `third_party/<repo>` | copia patchata in `build/patched/<metodo>/` | I submodule in `third_party/` restano intatti. `scripts/apply_patches.sh` copia ogni submodule in `build/patched/<metodo>/` (non versionata) e vi applica in ordine le patch di `patches/<metodo>/` con `git apply`. Il `PYTHONPATH` dei worker punta a `build/patched/<metodo>/` |
| Partizione predefinita SLURM | non specificata | `defq` è la predefinita e ha GPU **AMD** | **Ogni** job deve indicare esplicitamente la partizione; nessun job GPU su `defq` |
| Account e QoS SLURM | non previsti | obbligatori: `-A did_tesi_nlp_330 --qos=<qos della partizione>` | Campi `account` e `qos` nei profili `configs/cluster/*.yaml` |
| Montaggio delle immagini Apptainer | implicito | `squashfuse` assente: ogni `exec` su `.sif` converte l'immagine in una sandbox temporanea | Vedi §5: usare un'immagine in formato directory (`--sandbox`) per l'esecuzione dei test |
| Percorsi dei modelli | percorso locale per modello | modelli nella cache Hugging Face condivisa | Vedi §7 |
| Durata massima dei job SLURM | timeout del worker 24 h (`worker_timeout_s: 86400`) | **7 h** su `gpuq` e `aiq`, **9 h** su `defq` (partizione CPU) | Ogni cella deve stare comodamente nel limite (obiettivo ≤ 80%: 5,5 h GPU, 7 h CPU). Le celle troppo grandi vanno divise in blocchi (shard) di prompt o campioni; `worker_timeout_s` e `timeout_min` dei profili SLURM vanno impostati sotto il limite. La ripresa dei worker (SPEC §8.2) è obbligatoria, non opzionale |
| Numerazione delle patch | SPEC §9.4 cita `0001` per l'estrazione del notebook di Code Acrostic | i numeri si assegnano in ordine di creazione, per metodo | Code Acrostic: `0000` = CC.json dal fork; `0001` = estrazione del notebook in modulo. Il numero in SPEC era solo un esempio |
| Dipendenze patchate degli ambienti | non previste | `human-eval` e `mxeval` installati in modalità editable con `setup.py` modificato | Patch in `patches/_deps/<dipendenza>/`; sono solo documentazione (gli ambienti sono già installati), `apply_patches.sh` non le applica |

---

## 2. Utenti e percorsi

- Utenti che lavorano sul progetto: `i.cipriano1@studenti.unisa.it`, `s.faraulo@studenti.unisa.it` (gruppo `did_tesi_nlp_330`).
- Le home dei due utenti sono diverse: tutto ciò che serve ai job deve stare sotto `/mnt/beegfs/did_tesi_nlp_330/icipriano/`, **mai** nelle home.

```
/mnt/beegfs/did_tesi_nlp_330/icipriano/
├── repo_wm_bench/                 # repository Git del progetto (clone di lavoro sul cluster)
│   └── patches/                   # patch 0000 delle modifiche preesistenti (§9)
└── wm_bench/                      # risorse esterne al repository
    ├── bench-core/                # ambiente dell'orchestratore (a prefisso)
    ├── miniforge3/                # Miniforge; ambienti dei metodi in miniforge3/envs/
    ├── datasets/                  # $DATA
    ├── deps/                      # human-eval, mxeval (editable), nltk_data
    ├── hf_cache/                  # HF_HOME (hub/, accelerate/, xet/)
    ├── repos/shared/              # cloni originali dei metodi (solo riferimento, non usati dal framework)
    └── setup/                     # prove di installazione
```

Variabili usate negli script:

```bash
export PROJ=/mnt/beegfs/did_tesi_nlp_330/icipriano
export REPO=$PROJ/repo_wm_bench
export WMB=$PROJ/wm_bench
export DATA=$WMB/datasets
export HF_HOME=$WMB/hf_cache
export HF_HUB_CACHE=$WMB/hf_cache/hub
```

---

## 3. Interpreti Python degli ambienti

| Ambiente | Interprete | Python |
|---|---|---|
| bench-core | `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/bench-core/bin/python` | 3.11 |
| sweet | `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/miniforge3/envs/sweet/bin/python` | 3.10.21 |
| acw | `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/miniforge3/envs/acw/bin/python` | 3.10.21 |
| stone | `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/miniforge3/envs/stone/bin/python` | 3.9.19 |
| code_acrostic | `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/miniforge3/envs/code_acrostic/bin/python` | 3.10.21 |
| promptmark | `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/miniforge3/envs/promptmark/bin/python` | 3.10.21 |
| mcgmark | `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/miniforge3/envs/mcgmark/bin/python` | 3.10.21 |

Tutti gli ambienti dei metodi hanno torch compilato **solo per CUDA**.

Installazioni editable negli ambienti (da non rompere: le cartelle in `deps/` non vanno spostate né cancellate):

| Ambiente | Pacchetto editable | Percorso |
|---|---|---|
| acw | mxeval 1.0 | `wm_bench/deps/mxeval` |
| promptmark | mxeval 1.0 | `wm_bench/deps/mxeval` |
| code_acrostic | human-eval 1.0 | `wm_bench/deps/human-eval` |
| sweet, stone, mcgmark | nessuno | — |

---

## 4. SLURM

| Partizione | GPU | Nodi | Uso nel progetto |
|---|---|---|---|
| `gpuq` | 4 × NVIDIA A100 per nodo | gnode[01-14] | **Partizione GPU principale** (generazione, rilevazione SWEET, attacchi T2/T3, impercettibilità) |
| `aiq` | 8 × NVIDIA H100 per nodo | ainode[01-02] | GPU NVIDIA alternativa (l'account ha la QoS `did_tesi_nlp_330_aiq_qos`) |
| `defq` (predefinita) | 4 × AMD MI100 per nodo | tnode[01-16] | **Partizione CPU principale** (esecuzione dei test, attacchi T1/T4, metriche, report), **senza richiedere GPU**. **Mai per job GPU** (torch solo CUDA) |
| `fatq` | nessuna | fnode[01-05] | **Non utilizzabile** con l'account `did_tesi_nlp_330` salvo verifica contraria: l'account non ha una QoS per `fatq` (vedi sotto) |

- Account da usare: **`did_tesi_nlp_330`**. L'utente ha anche altre associazioni (`did_generative_ai_336`, `did_tesi_di_laurea_437`, `usershpc`) che **non** vanno usate per questo progetto.
- QoS dell'account (da `sacctmgr -P`): `did_tesi_nlp_330_aiq_qos`, `did_tesi_nlp_330_defq_qos`, `did_tesi_nlp_330_gpuq_qos`, `did_tesi_nlp_330_thinq_qos`, `normal`.

| Partizione | QoS da usare |
|---|---|
| `gpuq` | `did_tesi_nlp_330_gpuq_qos` |
| `aiq` | `did_tesi_nlp_330_aiq_qos` |
| `defq` | `did_tesi_nlp_330_defq_qos` |
| `fatq` | nessuna QoS dedicata: non usare |
| `thinq` | QoS presente, ma la partizione non compare in `sinfo`: non usare |

| Partizione | Limite di tempo per job |
|---|---|
| `aiq` | 7:00:00 |
| `gpuq` | 7:00:00 |
| `fatq` | 4:00:00 |
| `defq` | 9:00:00 |

Profili Hydra attesi: `configs/cluster/slurm_gpu.yaml` → `gpuq` con `did_tesi_nlp_330_gpuq_qos` (alternativa `slurm_gpu_h100.yaml` → `aiq` con `did_tesi_nlp_330_aiq_qos`); `configs/cluster/slurm_cpu.yaml` → `defq` con `did_tesi_nlp_330_defq_qos` e **nessuna** richiesta di GPU (`gres` assente). La `ResourcePolicy` deve rifiutare qualsiasi configurazione che mandi un job GPU su `defq`.

---

## 5. Apptainer

- Versione: **1.1.9**.
- Build con `--fakeroot`: **funziona** (utente non presente in `/etc/subuid`; Apptainer usa uno spazio dei nomi con mappatura su root). Verificato sul nodo di login `lnode01`.
- `--net --network none`: **funziona** (verificato su `lnode01`).
- `squashfuse` e `fuse2fs` **assenti**: a ogni `apptainer exec` su un file `.sif` l'immagine viene convertita in una sandbox temporanea. Con migliaia di esecuzioni questo costo è inaccettabile.
  - **Decisione richiesta all'agente (Milestone 4):** costruire l'immagine anche in formato directory (`apptainer build --sandbox containers/sandbox_dir containers/sandbox.def`) ed eseguire i test su quella; in alternativa eseguire più test per singola invocazione del container. Documentare la scelta in `docs/decisions/ADR-001-sandbox-network.md` insieme all'esito di `--network none`.
  - L'hash registrato negli `ExecutionRecord` resta quello del `.sif` da cui è derivata la directory.
- Da verificare in Milestone 4 anche sui nodi di calcolo `tnode` (partizione `defq`, usata per i job CPU), perché le prove sono state fatte solo sul nodo di login.

---

## 6. Rete

- I nodi di calcolo hanno accesso a internet in uscita: verificato su `defq` (`curl https://pypi.org` → HTTP 200).
- Conseguenza: Sourcery (ACW) può girare sui nodi di calcolo. Da riverificare su `gpuq` in Milestone 6 (su `defq` è già verificato).
- Nonostante l'accesso a internet, i job devono girare con `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` e `HF_DATASETS_OFFLINE=1` (riproducibilità). L'unica eccezione è Sourcery nel worker ACW.

---

## 7. Modelli

I modelli sono nella cache Hugging Face condivisa (`HF_HUB_CACHE=/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/hf_cache/hub`).

| `model_id` nel framework | Repository Hugging Face | Cartella in cache | Snapshot | Ruolo |
|---|---|---|---|---|
| `qwen25_coder_7b` | `Qwen/Qwen2.5-Coder-7B-Instruct` | `models--Qwen--Qwen2.5-Coder-7B-Instruct` | `c03e6d358207e414f1eca0bb1891e29f1db0e242` | generatore |
| `deepseek_coder_6p7b` | `deepseek-ai/deepseek-coder-6.7b-instruct` | `models--deepseek-ai--deepseek-coder-6.7b-instruct` | `e5d64addd26a6a1db0f9b863abf6ee3141936807` | generatore |
| `llama31_8b_attacker` | `meta-llama/Llama-3.1-8B-Instruct` | `models--meta-llama--Llama-3.1-8B-Instruct` | `0e9e39f249a16976918f6564b8830bc894c89659` | attaccante T2.2 / T2.3 |
| `starcoder2_7b` | `bigcode/starcoder2-7b` | `models--bigcode--starcoder2-7b` | `bb9afde76d7945da5745592525db122d4d729eb1` | osservatore per la perplexity |
| `unixcoder_base` | `microsoft/unixcoder-base` | `models--microsoft--unixcoder-base` | `5604afdc964f6c53782a6813140ade5216b99006` | classificatore avversario |

**Regola per l'agente:** nei file `configs/model/*.yaml` il campo `path` deve essere il percorso della **snapshot** (`<HF_HUB_CACHE>/<cartella in cache>/snapshots/<hash>`), in modo che tutti gli ambienti (anche quelli dei metodi, che non condividono la configurazione HF dell'orchestratore) carichino esattamente gli stessi pesi da un percorso locale. L'hash di snapshot va registrato nei manifest.

Esempio: `path: /mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242`

---

## 8. Dataset

Radice: `$DATA = /mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/datasets`

```
datasets/
├── codesearchnet/{python,java,javascript}/
├── humanevalpack/{python,js,java,cpp,go,rust}/
├── mbpp/{full,sanitized}/
├── thestack/data/cpp/
├── evalplus/                      # HumanEvalPlus-v0.1.10.jsonl, MbppPlus-v0.2.0.jsonl
├── Project_CodeNet.tar.gz         # archivio completo (7,8 GB), da conservare
└── Project_CodeNet/               # estrazione parziale: metadata/, problem_descriptions/, derived/
```

### 8.1 HumanEval+ e MBPP+ (EvalPlus)

- Versioni: `HumanEvalPlus-v0.1.10.jsonl` (164 problemi), `MbppPlus-v0.2.0.jsonl` (378 problemi). Conteggi verificati.
- Posizione: `$DATA/evalplus/HumanEvalPlus-v0.1.10.jsonl` (7,7 MB) e `$DATA/evalplus/MbppPlus-v0.2.0.jsonl` (2,6 MB). Copiati dalla cache di `i.cipriano1`.
- Il loader legge i file da `$DATA/evalplus/` (variabili d'ambiente di override di EvalPlus o lettura diretta del JSONL), **mai** dalla cache nella home. Le versioni `v0.1.10` e `v0.2.0` sono fissate nella configurazione.

### 8.2 HumanEvalPack

- Fonte: `bigcode/humanevalpack`, file Parquet.
- File: `$DATA/humanevalpack/{python,js,java,cpp,go,rust}/test-00000-of-00001.parquet`.
- **Nota:** la cartella di JavaScript si chiama `js`, non `javascript`. Il loader deve mappare `Language.JAVASCRIPT → "js"`.
- Usati: `java`, `cpp`, `js` (Livello 1). `python` solo come controllo incrociato degli ID con HumanEval+.

### 8.3 MBPP originale

- Fonte: `google-research-datasets/mbpp`, file Parquet.
- File: `$DATA/mbpp/full/{train,test,validation,prompt}-00000-of-00001.parquet` (insieme = 974 problemi). `$DATA/mbpp/sanitized/` non usata.
- Le soluzioni originali stanno nella colonna del codice di riferimento (nome esatto da verificare nel loader).

### 8.4 CodeSearchNet

- Fonte: `code-search-net/code_search_net`, ramo `main`, file Parquet.
- File: `$DATA/codesearchnet/{python,java,javascript}/{train,validation,test}-00000-of-00001.parquet`.
- Righe verificate:

| Linguaggio | train | validation | test |
|---|---|---|---|
| python | 412.178 | 23.107 | **22.176** |
| java | 454.451 | 15.328 | **26.909** |
| javascript | 123.889 | 8.253 | **6.483** |

  Gli split di test coincidono con il protocollo.
- Colonne: `repository_name`, `func_path_in_repository`, `func_name`, `whole_func_string`, `language`, `func_code_string`, `func_code_tokens`, `func_documentation_string`, `func_documentation_tokens`, `split_name`, `func_code_url`.
- Il loader deve verificare quale tra `whole_func_string` e `func_code_string` contiene la funzione completa così come scritta nel repository, e usare quella come codice dei negativi (decisione da documentare nel loader). `repository_name` permette di controllare la disgiunzione tra campioni usati per scopi diversi.
- Uso degli split: `test` → Livello 3 e negativi di test di L1 per Java/JS; `validation` → negativi aggiuntivi della parte di sviluppo; `train` → liste di frequenza di PromptMark e nomi "naturali" dell'attacco T1.4.

### 8.5 The Stack (campione C++)

- Fonte: `bigcode/the-stack-dedup` (v1, deduplicata), accesso controllato già accettato.
- Cartella: `$DATA/thestack/data/cpp/` (nel repository Hugging Face la cartella si chiama `cpp`).
- File scaricati: `data-00000-of-00110.parquet` (57.982 righe) e `data-00001-of-00110.parquet` (57.982 righe), totale 115.964 **file** sorgente (non funzioni).
- Colonne principali: `content` (sorgente), `ext`, `lang`, `size`, `hexsha`, `max_stars_repo_name`, `max_stars_repo_path`, `max_stars_repo_licenses`, `avg_line_length`, `max_line_length`, `alphanum_fraction` (più le varianti `max_issues_*` e `max_forks_*`).
- Le funzioni vanno estratte da `content` con tree-sitter. I diversi usi (prompt di test, negativi di test, negativi di sviluppo, liste PromptMark) devono essere **disgiunti per repository** (`max_stars_repo_name`), non solo per file, per evitare che codice quasi identico dello stesso progetto finisca in parti diverse.
- Uso: 500 prompt e 10.000 negativi di test del Livello 3; negativi aggiuntivi della parte di sviluppo e liste di frequenza PromptMark per C++ da campioni **disgiunti** da quelli di test.

### 8.6 Project CodeNet

- URL: `https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-project-codenet/1.0.0/Project_CodeNet.tar.gz` (circa 7,8 GB).
- Archivio: `$DATA/Project_CodeNet.tar.gz`, 7,8 GB, `gzip -t` superato. Da conservare: servirà per l'estrazione selettiva.
- Estratto in `$DATA/Project_CodeNet/`:

```
Project_CodeNet/
├── metadata/                      # 4.054 voci: un CSV per problema + problem_list.csv
├── problem_descriptions/          # pXXXXX.html
└── derived/
    ├── duplicates/
    │   ├── C/  C++/  Java/  Python/
    │   ├── README
    │   └── identical_problem_clusters
    └── input_output/
        ├── data/                  # casi di input/output per problema
        ├── no_solutions.txt
        ├── README.md
        └── unverified_accepted_solutions.txt
```

- **Non** estrarre `Project_CodeNet/data/` per intero (circa 14 milioni di file): in Milestone 9 l'agente fornisce l'elenco dei 250 problemi da estrarre.
- Regole per la selezione dei 250 problemi (Milestone 9):
  - escludere i problemi elencati in `no_solutions.txt` e quelli senza casi in `input_output/data/`;
  - non usare come negativi le sottomissioni elencate in `unverified_accepted_solutions.txt`;
  - **i problemi dello stesso cluster in `identical_problem_clusters` sono lo stesso problema ai fini dello split (I4)**: vanno nella stessa parte e se ne seleziona al massimo uno per cluster;
  - `duplicates/` (C, C++, Java, Python; manca JavaScript) va usata per escludere sottomissioni quasi duplicate dai negativi umani; per JavaScript la deduplicazione va fatta dall'agente (per esempio con hash del codice normalizzato) e documentata.

### 8.7 ClassEval e Tests4Py

- ClassEval: submodule `third_party/ClassEval` (aggiunto in Milestone 0).
- Tests4Py: rinviato alla Milestone 13 (Livello 4 provvisorio).

---

## 9. Repository originali dei metodi

Cloni originali (solo riferimento): `/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/repos/shared/`.
I submodule in `third_party/` vanno fissati **esattamente** ai commit qui sotto.

| Metodo | Submodule (URL) | Commit | Patch preesistenti in `patches/<metodo>/` | Esclusi dalle patch |
|---|---|---|---|---|
| SWEET | https://github.com/hongcheki/sweet-watermark | `853b47eb064c180beebd383302d09491fc98a565` | `0000`: `lm_eval/generation.py` | — |
| ACW | https://github.com/Noelle1831-k/ACW | `2236dc304478a31fe7cc7cc393527375024428db` | `0000`: 5 file in `source/` (`RQ4-get-results.py`, `folder_list.py`, `folder_to_jsonl.py`, `one_rule_format.py`, `refactor.py`) + `source/.sourcery.yaml` (regole Sourcery usate da ACW) | `source/G/` (codice generato dagli autori con GPT-4/GPT-4o/Qwen su APPS, HumanEval, MBPP), `source/apps/` (copia del dataset APPS), `__pycache__` |
| STONE | https://github.com/inistory/STONE-watermarking | `bb5d809c0c494a219411e861f2313cca2b9fd7b4` | `0000`: `stone_implementation/run.py`, `stone_implementation/evaluation/stem.py` | — |
| Code Acrostic | https://github.com/XHLin-gamer/code_acrostic (**originale**) | `83823697f01d6250e7fccd9fa7005bdcfb967ded` | `0000`: aggiunge `CC.json` preso dal fork (identico alla copia locale: nessuna patch di modifiche locali). `0001` riservata all'estrazione del notebook in modulo (Milestone 6) | file accessori del fork (LICENSE, immagini, `.gitignore`, README) |
| PromptMark | https://github.com/ahmedfahad04/promptmark | `c04c8f61db1f0ec7ba2f213f4aa1a15787af484e` | `0000`: `requirements.txt`, `scripts/evals/*` (7 file), `scripts/robustness/program_perturb_rename_comments.py`, `src/run_experiments.sh` | notebook `*.ipynb`, `datasets/core`, `datasets/humaneval_164.json`, `datasets/sanitized-mbpp-sample-100.json`, `output/`, `__pycache__` |
| MCGMark | https://github.com/KevinHeiwa/MCGMT | `eefa27b68747f5027c3121abcc506f3488eae990` | `0000`: 6 file in `Watermark/` (`Detection_Only.py`, `Watermark-MBPP.py`, `logs_counter.py`, `watermark.py`, `watermark_global.py`, `watermark_processor.py`) + `homoglyphs.py`, `normalizers.py`, `homoglyph_data/` (copiati da lm-watermarking) | `__pycache__` |

### 9.1 Code Acrostic: esito del confronto originale/fork (deviazione D5)

- Fork: https://github.com/xhaughearl/code_acrostic, commit `698479462548ed44919e7eba791298f1a161cc72`, nessuna modifica locale.
- `CC.ipynb` del fork è **identico** a quello dell'originale. Il fork aggiunge solo `CC.json`, `LICENSE`, `.gitattributes`, `.gitignore`, due immagini e un README diverso.
- Decisione: submodule = originale; dal fork si prende solo `CC.json` tramite patch documentata.
- `CC.json` della cartella di lavoro è identico a quello del fork.
- Il notebook `CC.ipynb` **usa** `CC.json` (2 occorrenze) e **importa MarkLLM** (`MarkLLM.utils.transformers_config`, `MarkLLM.watermark.kgw.kgw`): Code Acrostic è costruito sopra l'implementazione KGW di MarkLLM. MarkLLM è quindi una dipendenza **obbligatoria** (§9.2).

### 9.2 Dipendenze collegate

| Dipendenza | URL | Commit | Usata da | Gestione nel framework |
|---|---|---|---|---|
| lm-watermarking | https://github.com/jwkirchenbauer/lm-watermarking | `82922516930c02f8aa322765defdb5863d07a00e` | MCGMark (origine dei file homoglyph) | submodule di riferimento in `third_party/lm-watermarking` (solo tracciabilità; i file usati sono già nella patch di MCGMark) |
| MarkLLM | https://github.com/THU-BPM/MarkLLM | `e43009f3d197f8d10e865e2ff731ba1006d1c7d1` | Code Acrostic (importato da `CC.ipynb`) | **submodule obbligatorio** in `third_party/MarkLLM` a questo commit; il `PYTHONPATH` del worker di Code Acrostic deve renderlo importabile come `MarkLLM` (verificare nell'audit come lo importa il notebook) |
| bigcode-evaluation-harness | https://github.com/bigcode-project/bigcode-evaluation-harness | `8fc5bae6…` (HEAD del 1° ottobre 2026; hash completo in `.gitmodules` e ADR-002) | executor `humanevalpack` (SPEC §11.3) | submodule in `third_party/` |
| ClassEval | https://github.com/FudanSELab/ClassEval | `eaeac44d…` (HEAD del 1° ottobre 2026; hash completo in `.gitmodules` e ADR-002) | loader ed executor di ClassEval (Livello 3) | submodule in `third_party/` |
| human-eval | https://github.com/openai/human-eval | `6d43fb980f9fee3c892a914eda09951f772ad10d` | ambiente `code_acrostic` (editable) | `patches/_deps/human-eval/0000-setup.patch` (solo `setup.py`) |
| mxeval | https://github.com/amazon-science/mxeval | `e09974f990eeaf0c0e8f2b5eaff4be66effb2c86` | ambienti `acw` e `promptmark` (editable) | `patches/_deps/mxeval/0000-setup.patch` (solo `setup.py`) |
| nltk_data | — | — | da verificare nell'audit | cartella di dati in `wm_bench/deps/nltk_data`, non versionata |

### 9.3 Note per gli audit

- `human-eval` ha solo `setup.py` modificato: la riga `exec` in `human_eval/execution.py` è quindi probabilmente ancora commentata (comportamento predefinito del pacchetto). Se Code Acrostic usa human-eval per eseguire test, i suoi risultati originali non sono riproducibili così come sono. Va verificato nell'audit. Il framework comunque non usa human-eval: l'esecuzione dei test è fatta dagli executor di bench-core (SPEC §11).
- Il repository di STONE contiene al suo interno una copia di `bigcode-evaluation-harness` (cartella `STONE-watermarking/bigcode-evaluation-harness/`). Per l'executor `humanevalpack` il framework usa il submodule **ufficiale** di bigcode-evaluation-harness (SPEC §4). Nell'audit di STONE annotare se la copia interna differisce dall'ufficiale.
- ACW e MCGMark contengono cartelle di dati versionate nel loro repository (`ACW/dataset/`, `MCGMT/Data/`): fanno parte del commit e non delle patch.

---

## 10. Decisioni dell'utente

| Voce | Decisione |
|---|---|
| `global_seed` | **20261001** (fissato il 1° ottobre 2026; da non modificare più) |
| D1 | Messaggio MCGMark fisso a 24 bit |
| D2 | **Confermata:** tutte le soglie di decisione, compresa quella di ACW, si calibrano all'1% di FPR sui negativi umani di sviluppo; le soglie native non sono iperparametri |
| D3 | **Confermata:** SWEET con contesto vuoto per i negativi senza prompt |
| D4 | **Confermata:** regola unica di estrazione del codice dall'output chat |
| D5 | **Risolta:** `CC.ipynb` del fork identico all'originale; si usa l'originale (commit `83823697`). Dal fork si prende solo `CC.json`, tramite patch `0000` documentata (§9.1) |
| D6 | ACW solo su Python |
| D7 | **Decisa (vedi §10.1)** |
| D8 | **Confermata:** liste di frequenza di PromptMark da split di training disgiunti |
| D9 | Accoppiamento baseline/marcato approssimato: da dichiarare nella tesi |
| Prompt di attacco T2.2 | Lasciati vuoti per ora (`configs/prompt/attacks/*.txt` con segnaposto) |

### 10.1 Regola per D7 (soluzioni MBPP senza corrispettivo in MBPP+)

Vincolo dell'utente: nessuna fuga di informazione tra sviluppo e test.

1. Le soluzioni originali dei 378 problemi presenti in MBPP+ seguono lo split del rispettivo problema (vincolo I4).
2. I 596 problemi MBPP **non** presenti in MBPP+ non sono mai usati come prompt: compaiono solo come negativi umani. Si assegnano con la stessa proporzione di MBPP+ (72 / 378 ≈ 19,05% in sviluppo), in modo deterministico: si ordinano i loro `problem_key` per `derive_seed(global_seed, "split", problem_key)` e i primi `round(596 × 72 / 378) = 114` vanno in sviluppo, i restanti 482 in test.
3. Ogni `problem_key` (e quindi ogni soluzione) appartiene a una sola parte. Le soglie si calibrano solo sulla parte di sviluppo e l'FPR di test si misura su problemi mai visti in calibrazione.
4. L'agente verifica con un test che nessun `problem_key` compaia in entrambe le parti e che i totali siano 1.138 negativi umani L1 Python (164 + 974).

---

## 11. Voci ancora da completare

| # | Voce | Bloccante per |
|---|---|---|
| 1 | Selezione dei 250 problemi CodeNet ed estrazione selettiva di `data/` | Milestone 9 |
| 2 | Motivazioni delle modifiche preesistenti ad ACW (`source/refactor.py`) e MCGMark (`Watermark/watermark_global.py`) | Milestone 6 (audit) |
