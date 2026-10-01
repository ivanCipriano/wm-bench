# SPEC — Framework unificato di benchmark per watermarking di codice generato da LLM

> **Destinatario:** agente di programmazione (Cursor, Cline o simili).
> **Natura del documento:** specifica vincolante. Ogni scelta qui descritta è stata concordata con il responsabile della tesi. In caso di ambiguità, **fermati e chiedi**; non improvvisare.
> **Lingua:** prosa, docstring e commenti in italiano; identificatori, nomi di file, chiavi di configurazione e messaggi di log in inglese.

---

## 0. Istruzioni operative per l'agente

1. **Lavora per milestone** (§22), una alla volta, nell'ordine indicato. Una milestone è chiusa solo quando tutti i suoi criteri di accettazione passano.
2. **Non modificare mai** i file sotto `third_party/`. Ogni alterazione al codice originale dei metodi va prodotta come patch in `patches/<metodo>/NNNN-descrizione.patch` e documentata in `patches/<metodo>/README.md` (motivazione, impatto sul comportamento, test che la verifica).
3. **Non eliminare mai campioni.** Ogni fallimento diventa uno *stato* (§3, invariante I1). Un filtro che riduce il numero di righe è un bug.
4. **Ogni deviazione dal protocollo** (anche minima) va annotata in `docs/deviations.md` con ID progressivo `D<n>`, motivazione e impatto. Le deviazioni già note sono elencate in §23.
5. **Non inventare iperparametri, prompt, versioni o percorsi.** Dove la specifica indica `TODO(user)`, lascia il segnaposto e segnalalo nel riepilogo della milestone.
6. Prima di implementare un adapter, completa l'**audit** del repository corrispondente (§9.0) e salvalo in `docs/audit/<metodo>.md`.
7. Ogni modulo nuovo ha test (§21). Nessun merge di codice senza test verdi e `mypy --strict` pulito su `src/bench`.
8. Alla fine di ogni milestone produci un breve riepilogo: cosa è stato fatto, cosa resta aperto, quali `TODO(user)` richiedono una decisione.

---

## 1. Contesto e obiettivo

Il framework valuta **6 metodi di watermarking training-free** per codice generato da LLM, in modo equo e riproducibile, secondo il *Protocollo Sperimentale Benchmark* (di seguito "protocollo").

| Metodo | Famiglia | Repository originale |
|---|---|---|
| SWEET | logit processor (entropia selettiva) | `hongcheki/sweet-watermark` |
| ACW (Li) | post-hoc, trasformazioni di codice | `Noelle1831-k/ACW` |
| STONE | logit processor (token non sintattici) | `inistory/STONE-watermarking` |
| Code Acrostic | logit processor | `XHLin-gamer/code_acrostic` (originale; vedi §9.4) |
| PromptMark | black-box: prompt + feedback iterativo | `ahmedfahad04/PromptMark` |
| MCGMark | logit processor multi-bit | `KevinHeiwa/MCGMT` |

**Generatori:** `Qwen2.5-Coder-7B-Instruct`, `DeepSeek-Coder-6.7B-Instruct` (pesi già presenti sul cluster).
**Modelli ausiliari:** `Llama-3.1-8B-Instruct` (attaccante T2.2/T2.3), `StarCoder2-7B` (perplexity), `microsoft/unixcoder-base` (classificatore avversario).

**Requisito funzionale chiave:** si deve poter eseguire **un solo metodo** e **una sola fase** alla volta, oppure qualsiasi combinazione, fino alla pipeline completa.

---

## 2. Vincoli ambientali (non negoziabili)

| Vincolo | Valore |
|---|---|
| Scheduler | SLURM |
| Ambienti Python | **7 ambienti Miniforge**: `bench-core` (orchestratore) + uno per metodo (`env-sweet`, `env-acw`, `env-stone`, `env-acrostic`, `env-promptmark`, `env-mcgmark`). Gli ambienti dei metodi sono **già configurati** dall'utente e non vanno modificati salvo aggiunta della dipendenza stdlib-only `bench-contracts` (§5.1). |
| GPU | Cluster con NVIDIA e AMD; **tutti gli ambienti hanno torch solo CUDA** ⇒ ogni job GPU va su partizioni **NVIDIA**. |
| Container | **Apptainer 1.1.9** (Singularity non disponibile). Usato solo per eseguire codice generato (sandbox dei test). |
| Modelli | Pesi locali sul cluster; percorsi in `configs/model/*.yaml`. Nessun download a runtime. |
| Tracking | **Nessun** MLflow/W&B. Log, manifest e metriche su disco. |
| Configurazione | Hydra 1.3 + OmegaConf; validazione Pydantic v2 nell'orchestratore. |
| Artefatti | Parquet (tabelle) e JSONL (scambio con i worker). |
| Parsing | tree-sitter con grammatiche a versione fissata (§20). |
| Python | `bench-core`: 3.11. Ambienti dei metodi: versione **ignota a priori** ⇒ tutto il codice che gira nei worker deve essere compatibile con **Python ≥ 3.8** e usare **solo la stdlib** oltre alle dipendenze già presenti nell'ambiente del metodo. |

### 2.1 Parametri di decoding (fissi, identici per tutti i metodi e per la baseline)

| Parametro | Valore |
|---|---|
| temperature | 0.2 |
| top_p | 0.95 |
| max_new_tokens | 512 (Livello 1), 1024 (Livelli 2, 3, 4) |
| campioni per task (N) | 6 |
| formato prompt | chat template del modello Instruct, con **system prompt unico fisso** (`configs/prompt/system.txt`); PromptMark aggiunge le proprie istruzioni sopra |

Gli shim devono **sovrascrivere** qualunque parametro di decoding cablato nel codice originale. L'avvenuta sovrascrittura va verificata da un test (§21.4).

---

## 3. Principi architetturali e invarianti

### 3.1 Principi

- **SOLID.** Ogni classe ha una responsabilità (S). Nuovi metodi, attacchi e metriche si aggiungono registrando nuove classi, senza modificare l'orchestratore (O). Gli adapter rispettano i contratti delle interfacce base (L). Le capacità dei metodi sono interfacce separate (I). L'orchestrazione dipende da astrazioni iniettate, mai da implementazioni concrete (D).
- **Orchestratore + worker.** L'orchestratore (`bench-core`) non importa mai codice dei metodi. Comunica con i worker tramite **file** (contratto §8). Ogni worker gira nell'ambiente del proprio metodo ed esegue il **codice originale** attraverso uno *shim*.
- **Fasi idempotenti e riprendibili.** Ogni fase legge e scrive artefatti identificati da un hash di configurazione. Se l'output esiste ed è valido, la fase viene saltata (salvo `force=true`).
- **Dominio puro.** I modelli di dominio non eseguono I/O.

### 3.2 Invarianti (devono essere garantiti dal codice e verificati dai test)

| ID | Invariante |
|---|---|
| **I1** | **Conservazione dei campioni.** Il numero di righe in uscita da ogni fase è uguale a quello in ingresso (per le fasi 1→1) o a quello atteso (per le fasi 1→N, es. N=6 campioni per prompt). Errori, fallimenti di inserimento, codice rotto e timeout sono **stati**, mai righe rimosse. |
| **I2** | **Fallimento di inserimento = non rilevato.** Un campione con `embed_status=FAILED` conta come non rilevato nel TPR, a prescindere dal punteggio, e riceve il punteggio minimo nel calcolo dell'AUROC. |
| **I3** | **Soglie congelate.** Le soglie di rilevazione si calibrano **solo** sui negativi umani della parte di sviluppo e si salvano in un artefatto `ThresholdSet` prima di qualsiasi valutazione sul test. Il codice delle fasi di test non può accedere ai negativi di test per calibrare. |
| **I4** | **Divisione per problema.** Uno stesso `problem_key` appartiene alla stessa parte (dev/test) in tutti i linguaggi, dataset e varianti. |
| **I5** | **Test prima e dopo.** La correttezza si misura sul codice prima dell'inserimento (baseline), dopo l'inserimento e dopo ogni attacco. I campioni che non funzionano più dopo un attacco sono riportati con il loro stato. |
| **I6** | **Contaminazione.** I campioni di CodeSearchNet e The Stack portano `contamination_risk=True`; il reporter li esclude dalle conclusioni sulla correttezza funzionale e li usa solo per rilevabilità e robustezza. |
| **I7** | **Incertezza obbligatoria.** Ogni metrica riportata ha un intervallo di confidenza (§13.6). |
| **I8** | **Determinismo dei semi.** Ogni seme deriva da `derive_seed(global_seed, *parti)` (§18). Nessun uso di RNG globali non inizializzati. |
| **I9** | **Iperparametri fissi tra livelli.** La configurazione selezionata per `(metodo, modello, linguaggio)` sulla parte di sviluppo è usata invariata per tutti i livelli. |

---

## 4. Struttura delle directory

```
wm-bench/
├── pyproject.toml                    # pacchetto "bench" (orchestratore), Python 3.11
├── environment-bench-core.yml        # ambiente dell'orchestratore (§20)
├── README.md
├── SPEC.md                           # questo documento
├── .pre-commit-config.yaml
├── .gitmodules
│
├── configs/                          # Hydra
│   ├── config.yaml                   # defaults list principale
│   ├── paths/cluster.yaml            # radici: artifacts, models, datasets, tmp
│   ├── envs/envs.yaml                # percorso dell'interprete Python per ogni ambiente
│   ├── cluster/{local,slurm_gpu,slurm_cpu}.yaml
│   ├── model/{qwen25_coder_7b,deepseek_coder_6p7b,llama31_8b_attacker,starcoder2_7b,unixcoder_base}.yaml
│   ├── decoding/{level1,level234}.yaml
│   ├── prompt/{system.txt, user/<dataset>_<lang>.j2, attacks/*.txt}
│   ├── method/{sweet,acw,stone,acrostic,promptmark,mcgmark}.yaml
│   ├── hpo/{sweet,acw,stone,acrostic,promptmark,mcgmark}.yaml
│   ├── dataset/{humanevalplus,mbppplus,mbpp_original,humanevalpack,codenet,classeval,codesearchnet,thestack_cpp,tests4py}.yaml
│   ├── split/default.yaml
│   ├── attack/{t1_1_format,t1_2_lint,t1_3_comments,t1_4_rename,t1_5_local,t2_1_refactor,t2_2_llm_rewrite,t2_3_roundtrip,t2_4_truncate,t3_1_remark,t4_1_minify,t4_2_bundle}.yaml
│   ├── metrics/default.yaml
│   ├── stage/<nome_fase>.yaml
│   └── experiment/{smoke,l1_dev_sweep,full_test}.yaml
│
├── packages/
│   └── bench-contracts/              # SOLO stdlib, Python >= 3.8; installato in tutti e 7 gli ambienti
│       ├── pyproject.toml
│       └── bench_contracts/
│           ├── __init__.py
│           ├── enums.py              # stringhe costanti degli stati
│           ├── schema.py             # dataclass di richiesta/risposta dei worker
│           ├── jsonl.py              # lettura/scrittura JSONL in streaming
│           └── seeds.py              # derive_seed condiviso
│
├── src/bench/                        # orchestratore (gira solo in bench-core)
│   ├── cli.py                        # entry point Hydra: `bench`
│   ├── domain/{enums,models,ids,errors}.py
│   ├── config/{schema,builder,resources}.py
│   ├── registry.py
│   ├── store/{artifact_store,refs,hashing,manifest}.py
│   ├── lang/{parsers,line_counter,identifiers,scopes,comments}.py
│   ├── data/
│   │   ├── loaders/{base,humanevalplus,mbppplus,mbpp_original,humanevalpack,codenet,classeval,codesearchnet,thestack_cpp,tests4py}.py
│   │   ├── splitter.py
│   │   └── negatives.py
│   ├── generation/{prompt_builder,hf_generator,code_extractor}.py
│   ├── methods/
│   │   ├── base.py                   # MethodAdapter + capacità
│   │   ├── worker_client.py
│   │   ├── hparams.py
│   │   └── adapters/{sweet,acw,stone,acrostic,promptmark,mcgmark}.py
│   ├── execution/
│   │   ├── sandbox.py                # ApptainerSandbox
│   │   └── executors/{base,evalplus,humanevalpack,codenet,classeval,tests4py,syntax}.py
│   ├── detection/calibration.py
│   ├── attacks/
│   │   ├── base.py, composite.py, context.py, tools.py
│   │   ├── t1/{format,lint,comments,rename,local_edits}.py
│   │   ├── t2/{refactor,llm_rewrite,roundtrip,truncate}.py
│   │   ├── t3/remark.py
│   │   └── t4/{minify,bundle}.py
│   ├── metrics/
│   │   ├── base.py, uncertainty.py
│   │   ├── detectability.py, functional.py, capacity.py
│   │   └── imperceptibility/{adversarial_logreg,adversarial_unixcoder,perplexity,codebleu}.py
│   ├── hpo/{grid,criterion,selector}.py
│   ├── pipeline/
│   │   ├── stage.py, pipeline.py, facade.py, observers.py
│   │   └── stages/{prepare_data,generate_baseline,watermark,execute,detect,calibrate,tune,attack,imperceptibility,metrics,report}.py
│   └── report/{tables,plots}.py
│
├── shims/                            # gira nei 6 ambienti dei metodi; Python >= 3.8
│   └── bench_shims/
│       ├── _common/{base.py,runner.py,decoding.py,chat.py}
│       ├── sweet/{__init__.py,__main__.py,shim.py}
│       ├── acw/...
│       ├── stone/...
│       ├── acrostic/...
│       ├── promptmark/...
│       └── mcgmark/...
│
├── third_party/                      # git submodule fissati a commit
│   ├── sweet-watermark/
│   ├── ACW/
│   ├── STONE-watermarking/
│   ├── code_acrostic/
│   ├── PromptMark/
│   ├── MCGMT/
│   ├── bigcode-evaluation-harness/   # esecuzione HumanEvalPack
│   └── ClassEval/
├── patches/<metodo>/{NNNN-*.patch, README.md}
│
├── containers/
│   ├── sandbox.def                   # immagine Apptainer per eseguire codice
│   └── README.md
├── tools/
│   ├── js/{package.json,package-lock.json,eslint.config.mjs,.prettierrc.json}
│   ├── java/{fetch_jars.sh, rewrite/pom.xml}
│   └── cpp/{.clang-format,.clang-tidy}
├── scripts/{setup_submodules.sh,apply_patches.sh,setup_tools.sh,build_sandbox.sh,install_contracts.sh}
│
├── docs/
│   ├── audit/<metodo>.md
│   ├── deviations.md
│   └── decisions/ADR-NNN-*.md
│
├── artifacts/                        # NON versionato (.gitignore); radice da configs/paths
└── tests/
    ├── unit/  contract/  oracle/  property/  integration/
    └── fixtures/tiny/                # mini-dataset di smoke test (§21.5)
```

---

## 5. Modello di dominio

### 5.1 `bench-contracts` (stdlib-only, condiviso)

Pacchetto minimo installato con `pip install -e packages/bench-contracts` in **tutti** gli ambienti. Vincoli: Python ≥ 3.8, `from __future__ import annotations`, nessuna dipendenza esterna, niente `match`, niente `X | Y` nei tipi a runtime.

```python
# bench_contracts/enums.py
class EmbedStatus:          # costanti stringa, non Enum, per massima portabilità JSON
    OK = "OK"               # watermark inserito come previsto
    PARTIAL = "PARTIAL"     # inserito parzialmente (es. MCGMark: meno di 24 bit)
    FAILED = "FAILED"       # inserimento impossibile o errore -> conta come non rilevato (I2)
    NOT_APPLICABLE = "NOT_APPLICABLE"   # linguaggio non supportato (es. ACW su Java)

class DetectStatus:
    OK = "OK"
    FAILED = "FAILED"       # errore del rilevatore -> punteggio minimo
    NOT_APPLICABLE = "NOT_APPLICABLE"

class WorkerOp:
    EMBED = "embed"         # da prompt (metodi in generazione) o da codice (ACW)
    DETECT = "detect"
```

```python
# bench_contracts/schema.py
@dataclass
class WorkerRequest:
    schema_version: str               # es. "1.0"; il worker rifiuta versioni diverse
    op: str                           # WorkerOp
    method: str
    model_id: str                     # es. "qwen25_coder_7b"
    model_path: str                   # percorso locale dei pesi
    tokenizer_path: str
    hparams: Dict[str, Any]           # già tradotti nei nomi del repository originale
    key: int                          # chiave segreta del watermark
    key_id: str                       # "k1" (principale) o "k2" (T3.1)
    decoding: Dict[str, Any]          # temperature, top_p, max_new_tokens, n
    system_prompt: str
    items_path: str                   # JSONL di WorkerItem
    output_path: str                  # JSONL di WorkerResult (append, flush per riga)
    device: str                       # "cuda:0" | "cpu"
    log_path: str

@dataclass
class WorkerItem:
    item_id: str                      # sample_id (detect) o prompt_id (embed)
    language: str
    seed: int
    prompt_messages: Optional[List[Dict[str, str]]]   # chat già costruita dall'orchestratore
    code: Optional[str]               # input per ACW (embed) e per detect
    context_prompt: Optional[str]     # contesto per SWEET in detect (§9.1)
    expected_message: Optional[str]   # stringa di "0"/"1" per MCGMark
    n: int                            # numero di campioni da generare (embed da prompt)

@dataclass
class WorkerResult:
    item_id: str
    sample_index: Optional[int]       # 0..n-1 per embed da prompt
    status: str                       # EmbedStatus o DetectStatus
    raw_output: Optional[str]         # testo grezzo del modello (embed)
    code: Optional[str]               # codice marcato (ACW) — per gli altri lo estrae l'orchestratore
    score: Optional[float]            # detect: più alto = più probabilmente marcato
    native_decision: Optional[bool]   # decisione con la soglia nativa del metodo (solo diagnostica)
    decoded_message: Optional[str]    # MCGMark
    extra: Dict[str, Any]             # diagnostica specifica del metodo (z-score, iterazioni, ecc.)
    error: Optional[str]              # messaggio d'errore troncato a 2000 caratteri
    elapsed_s: float
```

`bench_contracts/seeds.py` contiene `derive_seed` (§18), identico per orchestratore e worker.

### 5.2 Modelli dell'orchestratore (Pydantic v2, `frozen=True`)

File `src/bench/domain/enums.py` e `models.py`. Questi modelli rispecchiano lo schema dei contratti ma aggiungono validazione. Un **test di contratto** (§21.2) verifica che i campi coincidano.

```python
class Language(StrEnum): PYTHON="python"; JAVA="java"; CPP="cpp"; JAVASCRIPT="javascript"
class Level(StrEnum): L1="L1"; L2="L2"; L3="L3"; L4="L4"
class Split(StrEnum): DEV="dev"; TEST="test"
class Source(StrEnum): HUMAN="human"; LLM_BASELINE="llm_baseline"; LLM_WATERMARKED="llm_watermarked"; ATTACKED="attacked"
class MethodFamily(StrEnum): LOGIT="logit"; PROMPT="prompt"; POST_HOC="post_hoc"
class ExecStatus(StrEnum):
    PASSED="PASSED"; FAILED="FAILED"; SYNTAX_ERROR="SYNTAX_ERROR"; COMPILE_ERROR="COMPILE_ERROR"
    RUNTIME_ERROR="RUNTIME_ERROR"; TIMEOUT="TIMEOUT"; NO_TESTS="NO_TESTS"
    EXTRACTION_FAILED="EXTRACTION_FAILED"; SANDBOX_ERROR="SANDBOX_ERROR"
class AttackStatus(StrEnum): APPLIED="APPLIED"; UNCHANGED="UNCHANGED"; NOT_APPLICABLE="NOT_APPLICABLE"; TOOL_ERROR="TOOL_ERROR"

class Problem(BaseModel):
    problem_key: str            # chiave canonica cross-linguaggio, es. "humaneval/42", "mbpp/17", "codenet/p02547"
    dataset: str
    level: Level
    language: Language
    split: Split
    prompt_text: str            # descrizione/firma del task come fornita dal dataset
    entry_point: str | None
    canonical_solution: str | None
    test_ref: str | None        # riferimento ai test (id nel dataset); None se non eseguibile
    contamination_risk: bool
    loc_to_generate: int | None # righe di codice secondo §10.3

class CodeSample(BaseModel):
    sample_id: str              # vedi §5.3
    problem_key: str
    dataset: str
    language: Language
    level: Level
    split: Split
    source: Source
    model_id: str | None
    method: str | None
    config_hash: str | None
    key_id: str | None
    sample_index: int | None
    seed: int | None
    raw_output: str | None
    code: str                   # stringa vuota se l'estrazione fallisce
    extraction_ok: bool
    embed_status: str | None    # EmbedStatus
    expected_message: str | None
    parent_id: str | None       # campione da cui deriva (baseline -> ACW, marcato -> attaccato)
    attack_id: str | None
    attack_params_hash: str | None
    attack_status: AttackStatus | None
    contamination_risk: bool

class ExecutionRecord(BaseModel):
    sample_id: str; status: ExecStatus; n_tests: int | None; n_passed: int | None
    duration_s: float; stderr_tail: str | None; executor: str; sandbox_image_hash: str

class DetectionRecord(BaseModel):
    sample_id: str; method: str; model_id: str; config_hash: str; key_id: str
    status: str; score: float | None; native_decision: bool | None
    decoded_message: str | None; bits_correct: int | None; extra: dict[str, Any]

class ThresholdSet(BaseModel):
    method: str; model_id: str; language: Language; config_hash: str
    target_fpr: float; threshold: float; achieved_fpr_dev: float
    n_negatives: int; underpowered: bool; created_at: datetime; negatives_ref: str

class MetricValue(BaseModel):
    name: str; value: float; ci_low: float | None; ci_high: float | None
    ci_method: str; n: int; cell: dict[str, str]   # chiavi della cella: metodo, modello, linguaggio, livello, attacco...
```

### 5.3 Identificatori

- `problem_key`: `"{famiglia_problema}/{id}"`. HumanEval+ e HumanEvalPack condividono `humaneval/{n}`; MBPP+ e MBPP originale condividono `mbpp/{task_id}`; CodeNet usa `codenet/{problem_id}`.
- `sample_id`: `xxh3_128` esadecimale della concatenazione canonica `source|problem_key|language|model_id|method|config_hash|key_id|sample_index|parent_id|attack_id|attack_params_hash` (campi assenti = stringa vuota). Funzione unica in `domain/ids.py`.
- `config_hash`: hash (12 caratteri) del JSON canonico (`sort_keys=True`, separatori compatti) degli **iperparametri effettivi** del metodo, della versione del contratto e del commit del submodule del metodo.

---

## 6. Mappa dei design pattern

| Pattern | Dove | Classe/i | Perché |
|---|---|---|---|
| **Adapter** | `methods/adapters/*` (lato orchestratore) + `shims/*` (lato worker) | `SweetAdapter`, …, `SweetShim`, … | Uniforma 6 codebase eterogenee dietro le stesse interfacce, senza riscriverle |
| **Interface Segregation (capacità)** | `methods/base.py` | `PromptEmbedder`, `CodeEmbedder`, `Detector`, `MessageCarrier` | Ogni metodo implementa solo ciò che sa fare; l'orchestratore interroga le capacità invece di usare `if method == ...` |
| **Template Method** | `MethodAdapter._run_worker`, `ShimBase.run` | — | Flusso comune (validazione, invocazione, normalizzazione stati, controllo I1) con passi specifici sovrascrivibili |
| **Proxy (remoto)** | `methods/worker_client.py` | `WorkerClient` | Nasconde il confine di processo e di ambiente |
| **Strategy** | attacchi, metriche, executor, criterio HPO, incertezza, estrazione del codice | `Attack`, `Metric`, `Executor`, `SelectionCriterion`, `UncertaintyEstimator`, `CodeExtractor` | Algoritmi intercambiabili scelti da configurazione |
| **Composite** | `attacks/composite.py` | `AttackChain` | Catene di attacchi trattate come un attacco singolo |
| **Registry + Factory** | `registry.py` | `Registry[T]`, decoratori `@register_*` | Estendibilità senza modificare codice esistente (Open/Closed) |
| **Builder** | `config/builder.py` | `ExperimentBuilder` | Costruzione validata e immutabile di `ExperimentConfig` da Hydra o da codice |
| **Facade** | `pipeline/facade.py` | `BenchmarkFacade` | API semplice per notebook e CLI |
| **Pipeline / Chain of Responsibility** | `pipeline/` | `Stage`, `Pipeline` | Fasi componibili, eseguibili singolarmente |
| **Repository** | `store/artifact_store.py` | `ArtifactStore` | Unico punto di accesso agli artefatti, con hashing e manifest |
| **Observer** | `pipeline/observers.py` | `StageObserver`, `TimingObserver`, `LoggingObserver`, `InvariantObserver` | Tempi, log e controlli di invarianti senza sporcare la logica delle fasi |
| **Dependency Injection** | `cli.py` / `facade.py` | composizione manuale | Le fasi ricevono le dipendenze (store, registry, client), non le costruiscono |

---

## 7. Interfacce principali (orchestratore)

Le firme sono vincolanti. Implementazioni e dettagli interni sono a discrezione dell'agente, purché rispettino §3.

### 7.1 Registry

```python
# src/bench/registry.py
T = TypeVar("T")

class Registry(Generic[T]):
    def __init__(self, kind: str) -> None: ...
    def register(self, name: str) -> Callable[[type[T]], type[T]]: ...   # decoratore; nome duplicato -> errore
    def get(self, name: str) -> type[T]: ...                              # nome ignoto -> errore con elenco dei disponibili
    def names(self) -> list[str]: ...

METHODS: Registry[MethodAdapter]  = Registry("method")
ATTACKS: Registry[Attack]         = Registry("attack")
METRICS: Registry[Metric]         = Registry("metric")
EXECUTORS: Registry[Executor]     = Registry("executor")
LOADERS: Registry[DatasetLoader]  = Registry("loader")
STAGES: Registry[Stage]           = Registry("stage")
```

L'import di `bench.methods.adapters`, `bench.attacks`, ecc. popola i registri (import espliciti in `__init__.py`, niente scoperta dinamica basata su filesystem).

### 7.2 Metodi: capacità e adapter

```python
# src/bench/methods/base.py
class MethodAdapter(ABC):
    name: ClassVar[str]
    family: ClassVar[MethodFamily]
    env_name: ClassVar[str]                       # chiave in configs/envs/envs.yaml
    supported_languages: ClassVar[frozenset[Language]]
    gpu_for_embed: ClassVar[bool]
    gpu_for_detect: ClassVar[bool]                # True per SWEET

    def __init__(self, cfg: MethodConfig, worker: WorkerClient) -> None: ...
    def supports(self, language: Language) -> bool: ...
    @abstractmethod
    def hparam_space(self) -> HParamSpace: ...            # caricato da configs/hpo/<metodo>.yaml
    @abstractmethod
    def to_native_hparams(self, hp: HParams) -> dict[str, Any]: ...   # nomi del protocollo -> nomi del repo
    def config_hash(self, hp: HParams) -> str: ...

    # Template Method: costruisce la WorkerRequest, invoca il worker, normalizza, verifica I1.
    def _run_worker(self, op: str, items: Sequence[WorkerItem], hp: HParams,
                    model: ModelSpec, key: KeyRef, run_dir: Path) -> list[WorkerResult]: ...

class PromptEmbedder(ABC):
    """Metodi che inseriscono il watermark durante la generazione (logit o prompt)."""
    @abstractmethod
    def embed_from_prompts(self, problems: Sequence[Problem], hp: HParams, model: ModelSpec,
                           key: KeyRef, run_dir: Path) -> list[CodeSample]: ...

class CodeEmbedder(ABC):
    """Metodi post-hoc che marcano codice esistente (ACW)."""
    @abstractmethod
    def embed_from_code(self, samples: Sequence[CodeSample], hp: HParams, key: KeyRef,
                        run_dir: Path) -> list[CodeSample]: ...

class Detector(ABC):
    @abstractmethod
    def detect(self, samples: Sequence[CodeSample], hp: HParams, model: ModelSpec,
               key: KeyRef, run_dir: Path) -> list[DetectionRecord]: ...

class MessageCarrier(ABC):
    """Metodi multi-bit (MCGMark)."""
    message_length: ClassVar[int]
    @abstractmethod
    def expected_message(self, sample: CodeSample) -> str: ...
```

Matrice delle capacità (da confermare con l'audit §9.0):

| Adapter | Base | PromptEmbedder | CodeEmbedder | Detector | MessageCarrier | Linguaggi | GPU embed | GPU detect |
|---|---|---|---|---|---|---|---|---|
| `SweetAdapter` | ✓ | ✓ | | ✓ | | da audit | ✓ | ✓ |
| `AcwAdapter` | ✓ | | ✓ | ✓ | | **solo Python** | ✗ | ✗ |
| `StoneAdapter` | ✓ | ✓ | | ✓ | | da audit | ✓ | ✗ (solo tokenizer) |
| `AcrosticAdapter` | ✓ | ✓ | | ✓ | | da audit | ✓ | da audit |
| `PromptMarkAdapter` | ✓ | ✓ | | ✓ | | da audit | ✓ | ✗ |
| `McgmarkAdapter` | ✓ | ✓ | | ✓ | ✓ | da audit | ✓ | da audit |

Per i linguaggi non supportati l'adapter non invoca il worker: produce righe con `embed_status=NOT_APPLICABLE`, che il reporter mostra come "N/A".

### 7.3 Worker client

```python
# src/bench/methods/worker_client.py
class WorkerClient:
    def __init__(self, envs: EnvRegistry, shims_root: Path, third_party_root: Path,
                 timeout_s: int, observers: Sequence[StageObserver]) -> None: ...
    def run(self, env_name: str, method: str, request: WorkerRequest,
            items: Iterable[WorkerItem]) -> list[WorkerResult]: ...
```

Comportamento richiesto in §8.

### 7.4 Dati

```python
class DatasetLoader(ABC):
    name: ClassVar[str]
    levels: ClassVar[frozenset[Level]]
    languages: ClassVar[frozenset[Language]]
    contamination_risk: ClassVar[bool]
    @abstractmethod
    def load_problems(self, language: Language) -> list[Problem]: ...      # split ancora non assegnato
    @abstractmethod
    def load_human_negatives(self, language: Language) -> list[CodeSample]: ...

class ProblemSplitter:
    def __init__(self, cfg: SplitConfig, seed: int) -> None: ...
    def assign(self, problems: Sequence[Problem]) -> dict[str, Split]: ...   # problem_key -> split (I4)
```

### 7.5 Generazione baseline

```python
class PromptBuilder:
    def build(self, problem: Problem, system_prompt: str) -> list[dict[str, str]]: ...   # messaggi chat

class CodeExtractor(ABC):
    @abstractmethod
    def extract(self, raw_output: str, problem: Problem) -> tuple[str, bool]: ...       # (codice, ok)

class HFBaselineGenerator:
    def __init__(self, model: ModelSpec, decoding: DecodingConfig) -> None: ...
    def generate(self, problems: Sequence[Problem], seeds: Mapping[str, int]) -> list[CodeSample]: ...
```

### 7.6 Esecuzione

```python
class Sandbox(ABC):
    @abstractmethod
    def run(self, cmd: list[str], workdir: Path, timeout_s: int, mem_mb: int) -> SandboxResult: ...

class ApptainerSandbox(Sandbox): ...     # §11

class Executor(ABC):
    name: ClassVar[str]
    @abstractmethod
    def supports(self, dataset: str, language: Language) -> bool: ...
    @abstractmethod
    def execute(self, samples: Sequence[CodeSample], problems: Mapping[str, Problem]) -> list[ExecutionRecord]: ...
```

### 7.7 Attacchi

```python
@dataclass(frozen=True)
class AttackContext:
    rng: random.Random
    problem: Problem | None
    parsers: ParserService
    tools: ToolRunner
    llm: LocalLLM | None                 # T2.2, T2.3
    method: MethodAdapter | None          # T3.1
    model: ModelSpec | None
    hp: HParams | None

@dataclass(frozen=True)
class AttackOutcome:
    code: str
    status: AttackStatus
    detail: dict[str, Any]

class Attack(ABC):
    attack_id: ClassVar[str]              # "T1.4"
    group: ClassVar[str]                  # "T1" | "T2" | "T3" | "T4"
    supported_languages: ClassVar[frozenset[Language]]
    requires_gpu: ClassVar[bool]
    def __init__(self, params: Mapping[str, Any]) -> None: ...
    def params_hash(self) -> str: ...
    @abstractmethod
    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome: ...

class BatchAttack(Attack):
    """Attacchi che lavorano meglio in batch (LLM, ri-marcatura)."""
    @abstractmethod
    def apply_batch(self, samples: Sequence[CodeSample], ctx_factory: Callable[[CodeSample], AttackContext]) -> list[AttackOutcome]: ...

class AttackChain(Attack):                # Composite
    def __init__(self, attacks: Sequence[Attack]) -> None: ...
```

### 7.8 Metriche e incertezza

```python
class Metric(ABC):
    name: ClassVar[str]
    @abstractmethod
    def compute(self, table: pd.DataFrame, ctx: MetricContext) -> MetricValue: ...

class UncertaintyEstimator(ABC):
    @abstractmethod
    def interval(self, table: pd.DataFrame, statistic: Callable[[pd.DataFrame], float],
                 alpha: float) -> tuple[float, float]: ...

class ClusterBootstrap(UncertaintyEstimator): ...   # ricampiona problem_key con reinserimento
class WilsonInterval(UncertaintyEstimator): ...
class ClopperPearsonInterval(UncertaintyEstimator): ...

class ThresholdCalibrator:
    def fit(self, negative_scores: np.ndarray, target_fpr: float, min_negatives: int) -> ThresholdSet: ...
```

### 7.9 HPO

```python
class SelectionCriterion(ABC):
    @abstractmethod
    def select(self, candidates: pd.DataFrame) -> SelectionResult: ...

class ConstrainedTprCriterion(SelectionCriterion):
    def __init__(self, max_pass1_drop_pp: float = 3.0) -> None: ...   # soglia provvisoria, da config

@dataclass(frozen=True)
class SelectionResult:
    config_hash: str
    hparams: dict[str, Any]
    tpr_dev: float
    pass1_drop_pp: float
    constraint_satisfied: bool           # False -> caso segnalato
    rationale: str
```

### 7.10 Fasi, pipeline, facade

```python
class Stage(ABC):
    name: ClassVar[str]
    resources: ClassVar[ResourceClass]   # CPU | GPU_NVIDIA (dipende anche dal metodo: vedi §16)
    @abstractmethod
    def inputs(self, cell: Cell) -> list[ArtifactRef]: ...
    @abstractmethod
    def outputs(self, cell: Cell) -> list[ArtifactRef]: ...
    @abstractmethod
    def run(self, cell: Cell, ctx: StageContext) -> None: ...

@dataclass(frozen=True)
class Cell:
    """Unità minima di lavoro."""
    method: str | None; model_id: str | None; language: Language | None
    level: Level | None; split: Split | None; config_hash: str | None
    attack_id: str | None; attack_params_hash: str | None

class Pipeline:
    def __init__(self, stages: Sequence[Stage], store: ArtifactStore, observers: Sequence[StageObserver]) -> None: ...
    def run(self, cells: Sequence[Cell], force: bool = False) -> PipelineReport: ...
    # Per ogni (stage, cell): se gli input mancano -> MissingInputError con il nome della fase da lanciare;
    # se gli output esistono e sono validi e force=False -> SKIPPED.

class BenchmarkFacade:
    def __init__(self, cfg: ExperimentConfig) -> None: ...     # compone tutte le dipendenze
    def prepare_data(self) -> None: ...
    def generate_baseline(self, models: Sequence[str] | None = None) -> None: ...
    def watermark(self, methods: Sequence[str] | None = None) -> None: ...
    def execute(self, target: str) -> None: ...                 # "baseline" | "watermarked" | "attacked"
    def detect(self, methods: Sequence[str] | None = None) -> None: ...
    def calibrate(self) -> None: ...
    def tune(self) -> None: ...
    def attack(self, attacks: Sequence[str] | None = None) -> None: ...
    def imperceptibility(self) -> None: ...
    def metrics(self) -> None: ...
    def report(self) -> None: ...
```

### 7.11 ArtifactStore

```python
@dataclass(frozen=True)
class ArtifactRef:
    kind: str                 # "problems" | "baseline" | "watermarked" | "execution" | "detection" | ...
    path: Path                # relativo alla radice artifacts/
    def manifest_path(self) -> Path: ...

class ArtifactStore:
    def exists(self, ref: ArtifactRef) -> bool: ...               # file + manifest con status=complete
    def read_table(self, ref: ArtifactRef) -> pd.DataFrame: ...
    def write_table(self, ref: ArtifactRef, df: pd.DataFrame, manifest: Manifest) -> None: ...  # scrittura atomica: tmp + rename
    def read_model(self, ref: ArtifactRef, cls: type[M]) -> M: ...
    def write_model(self, ref: ArtifactRef, obj: BaseModel, manifest: Manifest) -> None: ...
```

`Manifest` contiene: fase, cella, config risolta, `config_hash`, commit Git del repository e dei submodule, hash delle patch applicate, versioni dei pacchetti dell'ambiente (`pip freeze` serializzato e il suo hash) del core **e** del worker, hash dell'immagine Apptainer, job id SLURM, nodo, GPU, timestamp di inizio e fine, numero di righe in input e output, `status`.

---

## 8. Protocollo dei worker

### 8.1 Invocazione

```
<env_python> -m bench_shims.<metodo> --request <run_dir>/request.json
```

- `<env_python>` da `configs/envs/envs.yaml`, ad esempio:
  ```yaml
  envs:
    bench-core:     {python: /path/miniforge3/envs/bench-core/bin/python}
    env-sweet:      {python: /path/miniforge3/envs/env-sweet/bin/python}
    env-acw:        {python: /path/miniforge3/envs/env-acw/bin/python}
    # ... TODO(user): percorsi reali
  ```
- `PYTHONPATH` impostato da `WorkerClient` = `shims/` + `third_party/<repo>` (+ eventuali sottocartelle indicate dall'audit). Nessuna installazione degli shim negli ambienti.
- Variabili d'ambiente trasmesse: `CUDA_VISIBLE_DEVICES`, `HF_HOME`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`, `TOKENIZERS_PARALLELISM=false`, `PYTHONHASHSEED=0`, e solo per ACW `SOURCERY_TOKEN` (letto dall'ambiente dell'utente, **mai** scritto su disco o nei manifest).

### 8.2 Ciclo di vita del worker

1. Legge e valida la richiesta (`schema_version` uguale, altrimenti exit code 3).
2. Carica **una volta** modello e tokenizer (se servono). Un errore qui è fatale: exit code 2.
3. Legge `output_path` se esiste e salta gli `item_id` già presenti (**ripresa**).
4. Per ogni item: esegue l'operazione dentro `try/except`. Un'eccezione sul singolo item produce un `WorkerResult` con `status=FAILED` e `error` valorizzato; **non** interrompe il ciclo.
5. Scrive ogni risultato come riga JSONL con `flush()` + `os.fsync()`.
6. Exit code 0 se il ciclo si completa (anche con item falliti).

### 8.3 Responsabilità di `WorkerClient`

- Scrive `items.jsonl` e `request.json` in `run_dir`, avvia il processo, inoltra stdout/stderr in `log_path`.
- Timeout complessivo configurabile; alla scadenza termina il processo e **rilancia** una volta (la ripresa evita lavoro duplicato); al secondo fallimento solleva `WorkerError` con le ultime 50 righe di log.
- Al termine verifica I1: per `embed` da prompt, `n` risultati per item; per `detect` e `embed` da codice, un risultato per item. Righe mancanti sono riempite con `status=FAILED`, `error="missing_result"` e segnalate dall'`InvariantObserver`.
- Esegue nel worker un comando `--introspect` (opzionale per lo shim, obbligatorio da implementare) che restituisce `pip freeze`, versione di Python, versione di torch e CUDA, commit del submodule; il risultato va nel manifest.

### 8.4 Base comune degli shim

```python
# shims/bench_shims/_common/base.py  (Python >= 3.8, stdlib + dipendenze dell'ambiente del metodo)
class ShimBase(object):
    method = ""                                    # sovrascritto
    def setup(self, request):  # carica modello/tokenizer/risorse; applica decoding forzato
        raise NotImplementedError
    def embed(self, item):     # -> list[WorkerResult] (n risultati) oppure [WorkerResult] per ACW
        raise NotImplementedError
    def detect(self, item):    # -> WorkerResult
        raise NotImplementedError

# shims/bench_shims/_common/runner.py
def main(shim_cls):  # gestisce argparse, --introspect, ripresa, try/except per item, scrittura JSONL
    ...
```

Utilità comuni in `_common/`:
- `decoding.py`: costruisce i `GenerationConfig` di HF con i parametri di §2.1 e applica il seed per item (`torch.manual_seed(seed)` + `torch.cuda.manual_seed_all(seed)` prima di ogni chiamata a `generate` con `num_return_sequences=n`).
- `chat.py`: applica `tokenizer.apply_chat_template(messages, add_generation_prompt=True)`; i messaggi arrivano già costruiti dall'orchestratore.

---

## 9. Integrazione dei sei metodi

### 9.0 Audit obbligatorio (prima di ogni adapter)

Per ogni repository produci `docs/audit/<metodo>.md` con:
1. commit fissato e data;
2. punto d'innesto (classe/funzione da richiamare per inserimento e rilevazione, con percorso del file);
3. significato e nome nativo di ogni iperparametro del protocollo (§9.x), con valori di default del paper;
4. linguaggi effettivamente supportati e motivo (es. parser solo Python, insiemi di token sintattici solo per alcuni linguaggi);
5. come si calcola il punteggio di rilevazione e la sua direzione;
6. parametri di decoding cablati da sovrascrivere;
7. dipendenze esterne (rete, token, binari);
8. patch necessarie (elenco, con motivazione);
9. **definizione operativa** di `EmbedStatus.FAILED` e `PARTIAL` per quel metodo.

L'audit va **mostrato all'utente** prima di implementare l'adapter.

### 9.1 SWEET

- **Famiglia:** logit processor. **Ambiente:** `env-sweet`.
- **Iperparametri (protocollo → nativo, da audit):** soglia di entropia, frazione green list (γ), intensità del bias (δ).
- **Inserimento:** lo shim costruisce il processor SWEET di `sweet.py` e lo passa a `model.generate` con il decoding di §2.1. Bypassa gli script bash e il loop di `bigcode-evaluation-harness`.
- **Rilevazione:** richiede il **modello** per ricalcolare l'entropia dei token ⇒ `gpu_for_detect=True`. Il contesto di condizionamento è `item.context_prompt`:
  - positivi e negativi LLM: il prompt chat usato in generazione;
  - negativi umani con prompt disponibile (L1, L2, ClassEval): lo stesso prompt costruito dal `PromptBuilder` per quel problema;
  - negativi senza prompt (CodeSearchNet, The Stack): contesto vuoto. **Deviazione D3** (§23).
- **Punteggio:** z-score restituito dal rilevatore SWEET.
- **FAILED:** eccezione o nessun token sopra la soglia di entropia (lo z-score non è definito). **PARTIAL:** non usato.

### 9.2 ACW (Li)

- **Famiglia:** post-hoc. **Ambiente:** `env-acw`. **Linguaggi:** **solo Python**; per Java, C++ e JS l'adapter restituisce `NOT_APPLICABLE` senza invocare il worker.
- **Iperparametri:** numero di trasformazioni applicate. La "soglia di rilevamento" del protocollo **non** entra nella griglia: la soglia di decisione si calibra all'1% di FPR come per tutti gli altri metodi (§13.2). **Deviazione D2.**
- **Input:** campioni della **baseline centrale** (§10.5), ovvero codice LLM senza watermark (6 per prompt).
- **Dipendenza esterna:** Sourcery con login via `SOURCERY_TOKEN`. **Da verificare nell'audit:** se Sourcery richiede connessione di rete in esecuzione, la fase ACW va lanciata su nodi con accesso in uscita. `TODO(user)`: confermare la politica di rete dei nodi di calcolo.
- **Punteggio:** il punteggio continuo fornito dal rilevatore ACW (es. frazione o conteggio delle trasformazioni rilevate); se ACW fornisce solo una decisione binaria, documentarlo nell'audit e usare il conteggio sottostante.
- **FAILED:** nessuna trasformazione applicabile (codice invariato) o errore del tool.
- **Metrica specifica:** tasso di preservazione condizionato (§13.3).

### 9.3 STONE

- **Famiglia:** logit processor sui soli token non sintattici. **Ambiente:** `env-stone`.
- **Iperparametri:** γ, δ. Chiave = `hash_key` nativo.
- **Punto d'innesto:** `stone_implementation/` (basato su MarkLLM). Il notebook `AUROC_perplexity.ipynb` e la valutazione su Colab **non** si usano: il framework fa rilevazione e metriche per conto proprio.
- **Rilevazione:** solo tokenizer. **Punteggio:** z-score sui token non sintattici.
- **Nota:** il repository include processori di altri metodi (baseline). Non usarli al posto delle implementazioni ufficiali degli altri cinque metodi.

### 9.4 Code Acrostic

- **Repository:** l'**originale** `XHLin-gamer/code_acrostic`. **Primo compito della Milestone 0:** aggiungi entrambi i remote e lancia `git log --oneline upstream/main..fork/main` (fork = `xhaughearl/code_acrostic`). Se il fork non ha commit propri, usa l'originale e annotalo nell'audit. Se li ha, elencali con il loro diff e **fermati** per far decidere l'utente (eventuale deviazione da registrare).
- **Ambiente:** `env-acrostic`. **Iperparametri:** dimensione della lista di suggerimento, γ, δ.
- **Particolarità:** il codice è in `CC.ipynb`. Estrai le funzioni necessarie in un **modulo patch** (`patches/acrostic/0001-extract-notebook-to-module.patch`, che crea `third_party/code_acrostic/acrostic_core.py` in una copia di lavoro) generato da uno script riproducibile (`nbconvert` + rimozione delle celle di demo e di installazione). Lo shim importa solo quel modulo. Nessuna modifica alla logica algoritmica.
- **`CC.json`:** verificare nell'audit se è configurazione o dati e se va caricato.

### 9.5 PromptMark

- **Famiglia:** black-box (prompt + feedback). **Ambiente:** `env-promptmark`.
- **Iperparametri:** dimensione della green list delle iniziali, numero massimo di iterazioni di feedback, soglia di forza del watermark (parametro del **loop di inserimento**, quindi resta nella griglia).
- **Modello:** caricato **in-memory nel processo del worker** (HF `transformers`), non tramite server. Patch `patches/promptmark/0001-inprocess-hf-provider.patch` che aggiunge a `src/llm_providers.py` un provider `inprocess_hf` (stessa interfaccia degli altri provider; usa `apply_chat_template` e il decoding di §2.1). Il loop di feedback usa **lo stesso modello** generatore.
- **Liste di frequenza:** la stratificazione per frequenza delle iniziali va calcolata da codice umano **disgiunto** dai negativi di valutazione: usa lo **split di training di CodeSearchNet** (Python, Java, JS) e un campione separato di The Stack per C++, con seed fisso. Salva le liste come artefatto versionato.
- **Linguaggi:** verificare nell'audit come vengono estratti gli identificatori. Se l'estrazione è solo Python (es. modulo `ast`), serve una patch che usi tree-sitter con le stesse grammatiche fissate in §20, da installare in `env-promptmark` solo se l'utente approva.
- **Punteggio:** statistica del test sulle iniziali (orientata in modo che più alto = più marcato; se il rilevatore produce un p-value, usare `-log10(p)`).
- **FAILED:** nessun codice prodotto o nessun identificatore estraibile.

### 9.6 MCGMark

- **Famiglia:** logit processor multi-bit. **Ambiente:** `env-mcgmark`.
- **Iperparametri:** intensità del bias, soglia per la ridistribuzione dei token molto probabili. **Lunghezza del messaggio fissata a 24 bit** (decisione dell'utente) ⇒ esclusa dalla griglia. **Deviazione D1.**
- **Messaggio atteso:** stringa di 24 bit uniformi, generata da `derive_seed(global_seed, "mcgmark-msg", problem_key, language, model_id, sample_index)`. Per i **negativi** (umani e LLM) si deriva un messaggio atteso con la stessa funzione sui loro identificativi, così il punteggio è definito anche su di essi.
- **Punteggio:** **numero di bit estratti uguali al messaggio atteso**, intero in [0, 24]. Estrazione fallita ⇒ **0** (punteggio minimo).
- **FAILED:** estrazione impossibile. **PARTIAL:** il codice generato non ha abbastanza token idonei per contenere tutti i 24 bit (definizione operativa da audit).
- **Metriche di capacità:** §13.5.
- **Nota:** il README dichiara una "versione preliminare" e richiede modello e parametri da configurare a mano in `watermark.py`. Lo shim deve passare tutti i parametri esplicitamente, eventualmente con una patch che sostituisca le costanti cablate con argomenti.

### 9.7 Chiavi segrete

`key(method, key_id) = derive_seed(global_seed, "wm-key", method, key_id) % 2**31`. `k1` per tutti gli esperimenti; `k2` solo per T3.1. Se il metodo richiede un formato diverso (es. stringa), la conversione avviene in `to_native_hparams` ed è documentata nell'audit.

---

## 10. Dati

### 10.1 Dataset e loader

| Loader | Livello | Linguaggi | Fonte | Negativi umani | `contamination_risk` |
|---|---|---|---|---|---|
| `humanevalplus` | L1 | python | EvalPlus | soluzioni canoniche | False |
| `mbppplus` | L1 | python | EvalPlus | — (vedi `mbpp_original`) | False |
| `mbpp_original` | L1 | python | MBPP (974) | soluzioni originali | False |
| `humanevalpack` | L1 | java, cpp, javascript | `bigcode/humanevalpack` | canoniche + integrazione (§10.4) | False |
| `codenet` | L2 | python, java, cpp, javascript | Project CodeNet (locale) | sottomissioni accettate di altri utenti | False |
| `classeval` | L3 | python | ClassEval (submodule) | classi canoniche | False |
| `codesearchnet` | L3 | python, java, javascript | CodeSearchNet (locale) | funzioni dello split di test | **True** |
| `thestack_cpp` | L3 | cpp | The Stack (locale, accesso già accettato) | funzioni C++ campionate | **True** |
| `tests4py` | L4 (provvisorio) | python | Tests4Py 1.0.0 | codice originale | False |

Tutti i percorsi si leggono da `configs/dataset/*.yaml` (`TODO(user)` per i percorsi locali). Niente download a runtime.

### 10.2 Numerosità (dal protocollo)

| Livello e linguaggio | Prompt (dev / test) | Codice generato per modello | Negativi umani |
|---|---|---|---|
| L1 Python | 542 (96 / 446) | 3.252 | 1.138 |
| L1 Java, C++, JS (ciascuno) | 164 (24 / 140) | 984 | 164 + integrazione |
| L2 ciascun linguaggio | 250 (~50 / ~200) | 1.500 | 1.250 |
| L3 ClassEval | 100 (0 / 100) | 600 | 100 |
| L3 CodeSearchNet (ciascuno) | 500 (0 / 500) | 3.000 | intero split di test |
| L3 The Stack C++ | 500 (0 / 500) | 3.000 | 10.000 |
| L4 Tests4Py | pilota su 5 progetti | da calcolare | da calcolare |

Un test di integrazione verifica questi conteggi dopo `prepare_data` (con tolleranza solo dove il protocollo dice "circa").

### 10.3 Conteggio delle righe da generare

`lang/line_counter.py`: conta le righe che contengono almeno un token di codice, escludendo righe vuote, commenti e docstring, **della sola parte che il modello deve generare** (es. corpo della soluzione canonica, non la firma fornita nel prompt). Implementazione con tree-sitter: si raccolgono i numeri di riga dei nodi foglia non commento; per Python si escludono le stringhe che sono la prima istruzione di un modulo, classe o funzione (docstring). Test: deve riprodurre media e deviazione standard della tabella del protocollo per HumanEval+ (5,1 ± 4,4) e MBPP+ (4,0 ± 3,7) entro ±0,1.

### 10.4 Divisione per problema

`ProblemSplitter` (I4):
- I problemi HumanEval (`humaneval/0..163`) hanno **una sola** assegnazione dev/test valida per HumanEval+ e per tutti e tre i linguaggi di HumanEvalPack: **24 dev**, 140 test.
- MBPP+: **72 dev**, 306 test (così L1 Python = 24 + 72 = 96 dev).
- CodeNet: selezionare 250 problemi con almeno una sottomissione accettata in **tutti e quattro** i linguaggi e abbastanza negativi (5 sottomissioni accettate per linguaggio, per arrivare a 1.250); ~50 dev, ~200 test, stessa assegnazione nei 4 linguaggi.
- L3 e L4: solo test.
- Procedura: ordinare i `problem_key` per `derive_seed(global_seed, "split", problem_key)` e prendere i primi k come dev. Deterministica e documentata nel manifest.
- **Negativi MBPP extra** (problemi delle 974 soluzioni originali che non sono in MBPP+): assegnazione controllata da `split.mbpp_extra_assignment ∈ {proportional, dev, test}`; default `proportional`. **Deviazione D7**, `TODO(user)` conferma.
- **Integrazione dei negativi** (`data/negatives.py`):
  - test L1 Java/JS: funzioni di **lunghezza simile** da CodeSearchNet (split di test); C++: da The Stack. Lunghezza simile = stessa distribuzione di righe (§10.3), campionamento stratificato per decili.
  - dev (tutti i linguaggi): split di **validazione** di CodeSearchNet (Python, Java, JS) e un campione di The Stack C++ **disgiunto** da quello di test, per arrivare a `min_dev_negatives` (default 2.000, configurabile) campioni per linguaggio, così che una soglia all'1% di FPR sia stimabile.

### 10.5 Baseline centrale senza watermark

Generata **una sola volta** in `bench-core` (`HFBaselineGenerator`), per ogni modello, livello, linguaggio e split: 6 campioni per prompt con il decoding di §2.1. Usi:
1. input di ACW;
2. negativi prodotti da LLM (`Source.LLM_BASELINE`);
3. riferimento per ΔPass@1, Δperplexity, CodeBLEU e per il classificatore avversario.

Il seed di generazione è `derive_seed(global_seed, "gen", model_id, problem_key, language)`, **uguale** a quello passato ai worker dei metodi in generazione. In questo modo baseline e codice marcato dello stesso problema partono dallo stesso seme; l'accoppiamento resta approssimato (ambienti diversi possono avere flussi RNG diversi) e va dichiarato nella tesi.

### 10.6 Estrazione del codice

`CodeExtractor` applicato **in modo identico** alla baseline e a tutti i metodi in generazione:
1. primo blocco recintato (```` ``` ````) con tag del linguaggio atteso; altrimenti primo blocco senza tag; altrimenti l'intero testo se è sintatticamente valido (tree-sitter senza nodi `ERROR`);
2. per task a completamento di funzione, se il blocco non contiene la firma attesa, si antepone il prompt del dataset (regola documentata per dataset);
3. fallimento ⇒ `code=""`, `extraction_ok=False` ⇒ esecuzione `EXTRACTION_FAILED`, rilevazione con punteggio minimo.

`raw_output` si conserva sempre. La rilevazione avviene sul **codice estratto**.

---

## 11. Esecuzione dei test (sandbox)

### 11.1 Immagine Apptainer

`containers/sandbox.def` (bozza da completare in Milestone 4):

```
Bootstrap: docker
From: python:3.11-slim-bookworm

%post
    apt-get update && apt-get install -y --no-install-recommends \
        g++ make openjdk-17-jdk-headless nodejs npm ca-certificates curl && rm -rf /var/lib/apt/lists/*
    pip install --no-cache-dir evalplus==<stessa versione di bench-core>
    # dipendenze di ClassEval e dei test di CodeNet: da audit dei dataset
    mkdir -p /opt/jars   # eventuali jar richiesti dai test Java dell'harness
    # TODO: copiare in /opt/harness gli script di esecuzione di bigcode-evaluation-harness (dal submodule, in %files)

%environment
    export LC_ALL=C.UTF-8
    export PYTHONDONTWRITEBYTECODE=1

%labels
    wm-bench.sandbox.version 1
```

- Build: `apptainer build --fakeroot containers/sandbox.sif containers/sandbox.def` se `fakeroot` è abilitato sul cluster; altrimenti build su macchina con privilegi e copia del `.sif`. `TODO(user)`: verificare.
- L'hash SHA-256 del `.sif` entra in ogni `ExecutionRecord`.

### 11.2 `ApptainerSandbox.run`

```
apptainer exec --containall --cleanenv --no-home --pwd /work \
    --bind <workdir>:/work:rw [--net --network none] \
    containers/sandbox.sif timeout --kill-after=5 <t> prlimit --as=<mem> -- <cmd>
```

- `--net --network none` va provato in Milestone 4: se la configurazione del sito non lo consente agli utenti non privilegiati, si omette e si documenta (i nodi di calcolo devono comunque essere considerati). L'esito va in `docs/decisions/ADR-001-sandbox-network.md`.
- Ogni campione in una directory temporanea propria sotto `paths.tmp`, eliminata a fine esecuzione.
- Parallelismo: `ProcessPoolExecutor` con `max_workers = SLURM_CPUS_PER_TASK`.
- Timeout di default: 10 s per test Python L1, 20 s per Java/C++ (compilazione inclusa), configurabili per dataset.

### 11.3 Executor

| Executor | Dataset | Linguaggi | Note |
|---|---|---|---|
| `evalplus` | HumanEval+, MBPP+ | python | Pass@1 su test **base+plus** (EvalPlus) |
| `humanevalpack` | HumanEvalPack | java, cpp, javascript | logica di esecuzione `humanevalsynthesize-<lang>` di `bigcode-evaluation-harness` (programma completo + test) |
| `codenet` | CodeNet | tutti e 4 | stdin/stdout sui casi di input/output del problema; confronto con normalizzazione degli spazi finali |
| `classeval` | ClassEval | python | harness ufficiale `unittest` |
| `tests4py` | Tests4Py | python | CLI ufficiale; **provvisorio**, può richiedere un ambiente dedicato (`TODO(user)`) |
| `syntax` | CodeSearchNet, The Stack | tutti | nessun test: validità sintattica (tree-sitter) e compilazione (`javac`, `g++ -fsyntax-only`, `node --check`, `python -m py_compile`) |

Pass@1 con N = 6: stimatore non distorto `pass@1 = mean_problemi(c/n)`.

---

## 12. Attacchi di robustezza

Ogni attacco è una `Attack` registrata con il suo ID. I parametri vengono dalla configurazione `configs/attack/*.yaml`; ogni combinazione di parametri è una cella distinta (`attack_params_hash`). Dopo ogni attacco si eseguono **sempre** esecuzione dei test (o validità sintattica) e rilevazione (I5).

| ID | Implementazione | Configurazioni | Note di implementazione |
|---|---|---|---|
| **T1.1** Formattazione | `black` (py), `google-java-format` (java), `clang-format` con `tools/cpp/.clang-format` (cpp), `prettier` con `tools/js/.prettierrc.json` (js) | 1 | versioni fissate (§20); tool lanciati da `ToolRunner` con timeout |
| **T1.2** Lint con correzione | `ruff check --fix` regole default (py), `eslint --fix` con `tools/js/eslint.config.mjs` (js), `clang-tidy --fix` con `tools/cpp/.clang-tidy` e `-- -std=c++17` (cpp), OpenRewrite via `rewrite-maven-plugin` su progetto temporaneo (java) | 1 | OpenRewrite: dipendenze Maven pre-scaricate in `~/.m2` dal nodo di login (`scripts/setup_tools.sh`), esecuzione `-o` offline |
| **T1.3** Commenti | (a) rimozione di tutti i commenti; (b) inserimento di commenti | 2 | via nodi `comment` di tree-sitter; per Python anche le docstring in (a) **non** si rimuovono (sono stringhe) — documentare; testi dei commenti in (b) campionati da un pool di commenti umani (CodeSearchNet train) |
| **T1.4** Rinomina identificatori | percentuale r di variabili locali e parametri, nel rispetto dello scope; nomi casuali o naturali | r ∈ {25, 50, 75, 100} × {random, natural} = 8 | Python: `libcst` con `ScopeProvider`; Java/C++/JS: analisi di scope su tree-sitter limitata a **parametri e variabili locali** di funzione (mai campi, membri, globali, nomi esportati, `entry_point`). Nomi naturali da pool di identificatori di CodeSearchNet train per linguaggio. Nessuna collisione con parole chiave o nomi esistenti. |
| **T1.5** Modifiche localizzate | istruzioni senza effetto (variabile temporanea inutilizzata; log su **stderr**, mai su stdout perché CodeNet confronta stdout); estrazione di sottoespressioni in variabile temporanea | k ∈ {1, 3, 5} = 3 | punti d'inserimento scelti con l'RNG dell'attacco tra le istruzioni del corpo |
| **T2.1** Refactoring a regole | b trasformazioni casuali del motore AST; per Python anche le regole Ruff estese usate da ACW | b ∈ {1, 2, 3, all} = 4 | catalogo minimo per linguaggio (es. `for`↔`while`, inversione `if/else` con condizione negata, espansione di assegnazioni composte, scambio di operandi commutativi); catalogo in `attacks/t2/refactor.py`, regole Ruff estese da audit ACW |
| **T2.2** Riscrittura LLM | `Llama-3.1-8B-Instruct`, temperatura 0, prompt pubblicati: (a) refactoring conservativo; (b) riscrittura completa | 2 | prompt in `configs/prompt/attacks/{conservative,full_rewrite}.txt` (`TODO(user)`: testo definitivo); estrazione con `CodeExtractor`; HF in `bench-core`, GPU NVIDIA |
| **T2.3** Traduzione andata/ritorno (condizionale) | py→java→py, java→py→java, cpp→java→cpp, js→py→js | 1 per linguaggio | stesso LLM e decoding di T2.2; abilitata da `attack.t2_3.enabled` (default `false`) |
| **T2.4** Troncamento | prefisso del 75% o 50% delle istruzioni; su file, sottoinsieme di k funzioni | 2 + curva in k | istruzioni = figli diretti del corpo della funzione principale (tree-sitter); la curva in k solo per codice a livello di file (ClassEval, CodeNet multi-funzione) |
| **T3.1** Ri-marcatura con chiave diversa | ACW: riapplicazione con `k2`. Metodi in generazione: rigenerazione con stesso modello e metodo e chiave `k2` | 1 | `BatchAttack` che riceve l'adapter del metodo; rilevazione con **entrambe** le chiavi; si riporta quale watermark viene rilevato (k1, k2, entrambi, nessuno) |
| **T4.1** Minificazione | `terser` (js), `python-minifier` (py) | con e senza rinomina = 2 | solo py e js |
| **T4.2** Bundling | `esbuild --bundle` (js) | 1 | solo js |

Fuori ambito (dal protocollo): rinomina mirata, accesso al rilevatore, conoscenza della chiave, spoofing, composizione con codice umano.

Proprietà testate per ogni attacco (§21.3): determinismo dato il seed; conservazione della semantica sulle soluzioni canoniche (L1) salvo T2.2, T2.3, T2.4 che possono rompere il codice per definizione; stato `UNCHANGED` quando il codice non cambia.

---

## 13. Metriche

Tutte le metriche si calcolano **per cella** `(metodo, modello, linguaggio, livello, split, attacco)` e ognuna ha un intervallo di confidenza al 95% (I7).

### 13.1 Insiemi di valutazione

- **Positivi:** campioni `LLM_WATERMARKED` (o `ATTACKED` derivati da essi), inclusi `FAILED` e `PARTIAL` (I2).
- **Negativi umani:** usati per calibrare (dev) e per l'FPR (test).
- **Negativi LLM:** baseline senza watermark dello stesso modello; si riporta l'FPR su di essi come metrica separata, senza usarli per la calibrazione.

### 13.2 Calibrazione delle soglie (I3)

`ThresholdCalibrator.fit(neg_scores_dev, target_fpr=0.01, min_negatives=1000)`:
- decisione: `detected = score >= τ` e `embed_status != FAILED`;
- `τ` = il **più piccolo** valore candidato (valori distinti dei punteggi negativi più `+inf`) tale che `mean(neg >= τ) ≤ target_fpr`;
- con punteggi discreti (MCGMark, 0–24) l'FPR raggiunto può essere molto inferiore al target: si salva `achieved_fpr_dev` e lo si riporta. **Nessuna** randomizzazione della soglia;
- se `n_negatives < min_negatives` ⇒ `underpowered=True` e avviso nel report;
- `detect_status=FAILED` sui negativi ⇒ punteggio minimo (−∞), quindi mai falso positivo.

Una soglia per `(metodo, modello, linguaggio, config_hash)`, calcolata sui negativi umani dev di L1+L2 più l'integrazione di §10.4.

### 13.3 Rilevabilità e preservazione funzionale

| Metrica | Definizione | IC |
|---|---|---|
| `tpr_at_fpr` | frazione di positivi rilevati con la soglia congelata | bootstrap per problema |
| `fpr_human_test` | frazione di negativi umani di test sopra soglia (FPR effettivo) | Clopper–Pearson |
| `fpr_llm` | idem sui negativi LLM | Clopper–Pearson |
| `auroc` | `sklearn.metrics.roc_auc_score` su positivi vs negativi umani; `FAILED` ⇒ punteggio minimo | bootstrap per problema sui positivi + ricampionamento dei negativi |
| `pass_at_1` | stimatore non distorto, N = 6 | bootstrap per problema |
| `delta_pass_at_1_pp` | `pass@1(baseline) − pass@1(marcato)` in punti percentuali, accoppiato per problema | bootstrap per problema sulla differenza |
| `conditional_preservation` (solo ACW) | `P(corretto dopo trasformazione | corretto prima)` sui campioni baseline corretti | Wilson |
| `syntax_validity`, `compile_rate` | per dati senza test | Wilson |

### 13.4 Impercettibilità

- **Classificatore avversario** (due varianti, entrambe con `StratifiedGroupKFold(n_splits=5)` raggruppato per `problem_key`):
  - *positivi:* codice marcato; *negativi:* baseline dello stesso modello e linguaggio;
  - `adversarial_logreg`: regressione logistica (`C` scelto con CV interna) su feature esplicite: stile dei nomi (rapporti snake/camel/Pascal, lunghezza media, distribuzione delle iniziali), istogramma dei tipi di nodo tree-sitter, n-grammi di token 1–3 (TF-IDF, vocabolario limitato a 20.000);
  - `adversarial_unixcoder`: `microsoft/unixcoder-base` con testa di classificazione, max 512 token, lr 2e-5, 3 epoche, batch 16, early stopping su un 10% interno al fold di training;
  - si riportano l'AUROC medio ± deviazione standard tra i fold e l'AUROC sulle predizioni out-of-fold aggregate con IC bootstrap per problema. AUROC ≈ 0,5 indica impercettibilità.
- **Perplexity** con `StarCoder2-7B`: NLL medio per token sul solo codice (senza prompt), bf16, troncamento a 2.048 token. `delta_ppl` = differenza accoppiata per `(problem_key, sample_index)` tra marcato e baseline; si riportano media e mediana con IC bootstrap per problema.
- **CodeBLEU** (`codebleu`): marcato vs baseline accoppiato per `(problem_key, sample_index)`; per ACW il riferimento è il codice originale da cui deriva (accoppiamento esatto).

### 13.5 Capacità (MCGMark)

| Metrica | Definizione |
|---|---|
| `message_accuracy` | frazione di campioni con tutti i 24 bit corretti |
| `bit_accuracy` | media di `bits_correct / 24` |
| `embedding_success_rate` | frazione con `embed_status == OK` |

### 13.6 Incertezza

- `ClusterBootstrap`: 1.000 ricampionamenti (configurabile) dei `problem_key` con reinserimento, intervallo percentile 95%, seed derivato da `(global_seed, "bootstrap", nome_metrica, cella)`.
- Proporzioni senza struttura per problema: Wilson; FPR: Clopper–Pearson.

### 13.7 Efficienza

`TimingObserver` registra i tempi per fase, cella e campione (tempo di parete del worker, `elapsed_s` per item). Il report li mostra **con la nota del protocollo** (cluster condiviso, confronti non perfettamente equi).

---

## 14. Selezione degli iperparametri

1. **Griglie** in `configs/hpo/<metodo>.yaml`, con i nomi del protocollo. Valori iniziali: `TODO(user)` dopo l'audit (l'agente propone griglie centrate sui default dei paper e le sottopone all'utente).
2. **Sweep:** per ogni configurazione della griglia, sulla parte **dev** di L1 e L2 di quel linguaggio: `watermark → execute → detect (positivi + negativi dev) → calibrate`.
3. **Candidati:** tabella con `config_hash, hparams, tpr_dev, pass1_dev, pass1_baseline_dev, pass1_drop_pp`.
4. **Criterio** (`ConstrainedTprCriterion`, `max_pass1_drop_pp` da config, default 3.0 e marcato come provvisorio):
   - ammissibili = candidati con `pass1_drop_pp ≤ max`;
   - se ce ne sono: massimo `tpr_dev`; a parità di TPR (stesso numero di rilevati), minimo `pass1_drop_pp`; a ulteriore parità, `config_hash` lessicograficamente minore (deterministico);
   - altrimenti: minimo `pass1_drop_pp`, `constraint_satisfied=False`, caso **segnalato** nel report.
5. **Output:** `artifacts/hpo/selected/<metodo>/<modello>/<linguaggio>.yaml` + tabella completa dei candidati. La configurazione scelta resta fissa per tutti i livelli (I9).
6. Per ACW il ΔPass@1 confronta il codice trasformato con la baseline da cui deriva.

---

## 15. Fasi, artefatti e flusso dei dati

### 15.1 Flusso

```
prepare_data ──► generate_baseline ──► execute(baseline)
       │                 │
       │                 ├──────────────► watermark(ACW, da codice)
       │                 │
       └─────────────────┴──► watermark(metodi in generazione, da prompt)
                                     │
                                     ▼
                         execute(watermarked) ──► detect(pos, neg_human, neg_llm)
                                                        │
                                       calibrate(dev) ◄─┘
                                             │
                                        tune(dev) ──► config selezionate
                                             │
                    attack ──► execute(attacked) ──► detect(attacked)
                                             │
                         imperceptibility ──► metrics ──► report
```

### 15.2 Tabella delle fasi

| Fase | Ambiente | Risorse | Input | Output |
|---|---|---|---|---|
| `prepare_data` | core | CPU | dataset grezzi | `data/problems/*.parquet`, `data/splits/split.parquet`, `data/negatives/*.parquet`, `data/promptmark_freq/*.json` |
| `generate_baseline` | core | GPU NVIDIA | problemi | `baseline/<modello>/<L>_<lang>_<split>.parquet` |
| `watermark` | worker metodo | GPU (no ACW) | problemi o baseline, hparams | `watermarked/<metodo>/<modello>/<cfg>/<L>_<lang>_<split>.parquet` |
| `execute` | core + Apptainer | CPU | campioni | `execution/<origine>/.../*.parquet` (stessa chiave dei campioni) |
| `detect` | worker metodo | GPU solo SWEET (+ da audit) | campioni | `detection/<metodo>/<modello>/<cfg>/<soggetto>/*.parquet`, soggetto ∈ {`positives`, `neg_human`, `neg_llm`, `attacked/<attack_id>/<params_hash>`} |
| `calibrate` | core | CPU | detection dev | `thresholds/<metodo>/<modello>/<lang>/<cfg>.json` |
| `tune` | core | CPU | detection + execution dev | `hpo/selected/...`, `hpo/candidates/...` |
| `attack` | core (+ worker per T3.1) | CPU; GPU per T2.x e T3.1 | campioni marcati test | `attacked/<attack_id>/<params_hash>/<metodo>/<modello>/<cfg>/*.parquet` |
| `imperceptibility` | core | GPU | marcati + baseline | `imperceptibility/<metrica>/...` |
| `metrics` | core | CPU | tutto | `metrics/metrics.parquet` (una riga per `MetricValue`) |
| `report` | core | CPU | metriche | `reports/<data>/` tabelle CSV/LaTeX e figure |

Accanto a ogni file di dati c'è `<file>.manifest.json` (§7.11). Un file senza manifest con `status=complete` è considerato **inesistente**.

---

## 16. Configurazione Hydra e SLURM

### 16.1 `configs/config.yaml`

```yaml
defaults:
  - paths: cluster
  - envs: envs
  - cluster: local
  - decoding@decoding.level1: level1
  - decoding@decoding.level234: level234
  - split: default
  - metrics: default
  - _self_

global_seed: 20260101          # TODO(user): valore definitivo, poi non più modificabile
stage: ???                     # obbligatorio: nome della fase
methods: [sweet, acw, stone, acrostic, promptmark, mcgmark]
models: [qwen25_coder_7b, deepseek_coder_6p7b]
languages: [python, java, cpp, javascript]
levels: [L1, L2]
splits: [dev]
attacks: []
force: false
hpo:
  use_selected: true           # false durante lo sweep
  max_pass1_drop_pp: 3.0       # provvisorio (protocollo)
detection:
  target_fpr: 0.01
  min_negatives: 1000
```

### 16.2 Esempi di file

```yaml
# configs/model/qwen25_coder_7b.yaml
model_id: qwen25_coder_7b
path: ${paths.models}/Qwen2.5-Coder-7B-Instruct      # TODO(user)
tokenizer_path: ${.path}
dtype: bfloat16
chat_template: native

# configs/method/sweet.yaml
name: sweet
env: env-sweet
submodule: third_party/sweet-watermark
worker_timeout_s: 86400

# configs/hpo/sweet.yaml   (valori TODO(user) dopo l'audit)
space:
  entropy_threshold: [TODO]
  gamma: [TODO]
  delta: [TODO]

# configs/attack/t1_4_rename.yaml
attack_id: T1.4
grid:
  ratio: [0.25, 0.5, 0.75, 1.0]
  name_kind: [random, natural]
```

### 16.3 SLURM

- Launcher: `hydra-submitit-launcher` (`hydra/launcher=submitit_slurm`). Configurazioni `cluster/slurm_gpu.yaml` (partizione NVIDIA, `gres: gpu:1`, `cpus_per_task: 8`, `mem_gb: 64`, `timeout_min`) e `cluster/slurm_cpu.yaml`. Nomi delle partizioni: `TODO(user)`.
- `config/resources.py` contiene una `ResourcePolicy` che, data `(fase, metodo, attacco)`, restituisce `CPU` o `GPU_NVIDIA` (es. `detect`+`sweet` ⇒ GPU; `detect`+`stone` ⇒ CPU). Il comando `bench submit` sceglie automaticamente il profilo `cluster/*` corretto.
- **Mai** richiedere GPU AMD: gli ambienti hanno torch solo CUDA. La `ResourcePolicy` lo impone con un vincolo (`constraint`/`partition`) da configurazione.
- Sweep HPO e celle multiple ⇒ `--multirun`, che diventa un array SLURM.

### 16.4 CLI

```bash
# una fase, un metodo, una cella
bench stage=watermark methods=[sweet] models=[qwen25_coder_7b] languages=[python] levels=[L1] splits=[dev]

# sweep HPO di un metodo (array SLURM)
bench --multirun stage=watermark methods=[stone] hpo.use_selected=false hpo.grid=stone cluster=slurm_gpu

# esecuzione automatica con risorse scelte dalla policy
bench submit stage=detect methods=[sweet] levels=[L1] splits=[dev]

# verifica dell'installazione
bench doctor

# pipeline completa di smoke test su fixture
bench +experiment=smoke stage=all
```

`bench doctor` controlla: esistenza e versione degli interpreti dei 7 ambienti; `import bench_contracts` in ognuno; disponibilità di CUDA negli ambienti GPU (solo su nodo con GPU); versioni dei tool degli attacchi; `apptainer --version` = 1.1.9 e presenza del `.sif`; percorsi di modelli e dataset; variabile `SOURCERY_TOKEN` (solo presenza); stato dei submodule e delle patch.

---

## 17. Osservatori, log, errori

- `LoggingObserver`: logging standard (`logging`), un file per fase e cella in `artifacts/_logs/`, livello da config. **Vietato** `print` in `src/bench`.
- `TimingObserver`: §13.7.
- `InvariantObserver`: controlla I1 dopo ogni fase (righe in ingresso vs in uscita) e solleva `InvariantViolation` (fatale).
- Gerarchia delle eccezioni in `domain/errors.py`: `BenchError` → `ConfigError`, `MissingInputError`, `WorkerError`, `SandboxError`, `ToolError`, `InvariantViolation`, `ContractVersionError`.

---

## 18. Riproducibilità e semi

```python
# bench_contracts/seeds.py
def derive_seed(*parts: object) -> int:
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**31 - 1)
```

| Uso | Parti |
|---|---|
| split | `global_seed, "split", problem_key` |
| generazione | `global_seed, "gen", model_id, problem_key, language` |
| chiavi | `global_seed, "wm-key", method, key_id` |
| messaggi MCGMark | `global_seed, "mcgmark-msg", problem_key, language, model_id, sample_index` |
| attacchi | `global_seed, "attack", attack_id, params_hash, sample_id` |
| bootstrap | `global_seed, "bootstrap", metric_name, cell_key` |
| CV classificatori | `global_seed, "cv", classifier, cell_key` |
| campionamenti di dati | `global_seed, "sample", dataset, language, scopo` |

Inoltre: `torch.backends.cudnn.deterministic=True` dove non penalizza troppo; il determinismo bit a bit su GPU **non** è garantito e va dichiarato.

---

## 19. Convenzioni di codice

- **Python 3.11** in `src/bench`; **Python ≥ 3.8 compatibile** in `packages/bench-contracts` e `shims/`.
- Type hints ovunque. `mypy --strict` su `src/bench` e `packages/bench-contracts`; sugli shim `mypy` non strict (le librerie dei metodi non sono tipizzate).
- `ruff` per lint e formattazione (`ruff format`, riga 100). Regole: `E,F,W,I,B,UP,N,SIM,RUF,PL` con eccezioni motivate in `pyproject.toml`. Negli shim `UP` è configurato per target `py38`.
- Modelli Pydantic `frozen=True`; dataclass `frozen=True` per i valori.
- Nessuno stato globale mutabile salvo i registri (popolati solo all'import).
- Funzioni pure nel dominio e nelle metriche; I/O solo in `store/`, `data/loaders/`, `execution/`, `methods/worker_client.py`.
- Docstring in italiano, stile Google, obbligatorie per classi e funzioni pubbliche. Identificatori, log e chiavi in inglese.
- Nessun percorso assoluto nel codice: tutto da `configs/paths`.
- Nessun segreto nei file versionati o nei manifest.
- `pre-commit`: ruff, ruff-format, mypy, controllo dei file grandi, divieto di commit sotto `artifacts/`.
- Commit atomici con messaggio `[M<n>] <area>: <descrizione>`.
- Ogni decisione architetturale non banale presa durante l'implementazione ⇒ ADR breve in `docs/decisions/`.

---

## 20. Ambiente `bench-core`

### 20.1 Pacchetti

Il file pronto è `environment-bench-core.yml` (consegnato insieme a questo documento). Le versioni sono fissate per riproducibilità; **la Milestone 0 deve verificare** che l'ambiente si risolva (`pip check`), poi congelarlo in `requirements-bench-core.lock` (`pip freeze`) e `conda-bench-core.lock` (`conda list --explicit`). Se una versione non si risolve, l'agente propone la più vicina compatibile e lo annota in un ADR.

| Area | Pacchetti |
|---|---|
| Toolchain (conda-forge) | `python=3.11`, `nodejs=20`, `openjdk=17`, `maven=3.9`, `clang-tools=18` (clang-format, clang-tidy), `cxx-compiler`, `git` |
| Deep learning | `torch==2.4.1` (wheel CUDA 12.1), `transformers==4.46.3`, `accelerate==1.1.1`, `safetensors`, `tokenizers`, `sentencepiece` |
| Configurazione | `hydra-core==1.3.2`, `omegaconf==2.3.0`, `hydra-submitit-launcher==1.2.0`, `submitit`, `pydantic==2.9.2` |
| Dati | `numpy==1.26.4`, `pandas==2.2.3`, `pyarrow==17.0.0`, `datasets==3.1.0`, `orjson`, `xxhash`, `filelock` |
| Statistica e ML | `scipy==1.14.1`, `scikit-learn==1.5.2` |
| Valutazione | `evalplus==0.3.1`, `codebleu[all]==0.7.0` |
| Parsing | `tree-sitter` + grammatiche python/java/cpp/javascript **alle versioni richieste da `codebleu==0.7.0`** (una sola versione di tree-sitter nell'ambiente; congelata nel lockfile), `libcst==1.5.0` |
| Attacchi Python | `black==24.10.0`, `ruff==0.7.4`, `python-minifier==2.11.3` |
| Report | `matplotlib`, `tqdm` |
| Sviluppo e test | `pytest`, `pytest-cov`, `pytest-timeout`, `hypothesis`, `mypy`, `pandas-stubs`, `types-PyYAML`, `pre-commit` |
| Pacchetto locale | `pip install -e packages/bench-contracts` e `pip install -e .` |

### 20.2 Tool non Python (installati da `scripts/setup_tools.sh`, sul nodo di login)

- `tools/js/package.json` con versioni esatte e `package-lock.json`: `prettier@3.3.3`, `eslint@9.14.0`, `@eslint/js@9.14.0`, `globals`, `terser@5.36.0`, `esbuild@0.24.0`. Installazione con `npm ci` dentro `tools/js/`; il `ToolRunner` usa `tools/js/node_modules/.bin/*`.
- `google-java-format` 1.24.0 (jar `all-deps`) scaricato in `tools/java/` da `fetch_jars.sh` con verifica SHA-256.
- OpenRewrite: `tools/java/rewrite/pom.xml` con `rewrite-maven-plugin` e `rewrite-static-analysis` a versioni fissate; `mvn dependency:go-offline` sul nodo di login per popolare `~/.m2`.

### 20.3 Altri ambienti

Negli ambienti dei metodi l'**unica** aggiunta consentita è `pip install -e packages/bench-contracts` (`scripts/install_contracts.sh`). Le patch che richiedono nuove dipendenze (es. tree-sitter in `env-promptmark`) vanno approvate dall'utente.

---

## 21. Strategia di test

### 21.1 Livelli

| Tipo | Cartella | Marker pytest | Dove gira |
|---|---|---|---|
| Unit | `tests/unit` | — | ovunque, CPU, < 2 min |
| Contratto | `tests/contract` | `contract` | CPU; lancia ogni ambiente con `--introspect` |
| Proprietà | `tests/property` | — | CPU (Hypothesis) |
| Oracle (fedeltà) | `tests/oracle` | `gpu`, `oracle` | nodo NVIDIA |
| Integrazione | `tests/integration` | `apptainer`, `gpu`, `slurm` | cluster |

### 21.2 Test di contratto

- I campi delle dataclass di `bench_contracts.schema` coincidono con quelli dei modelli Pydantic corrispondenti.
- Round-trip JSONL: orchestratore → worker fittizio → orchestratore senza perdita.
- Per ognuno dei 6 ambienti: `python -c "import bench_contracts"` e `python -m bench_shims.<metodo> --introspect` terminano con exit code 0.
- Un worker che riceve una `schema_version` diversa termina con exit code 3.

### 21.3 Test di proprietà e unit (minimo richiesto)

- `derive_seed`: deterministico e stabile tra Python 3.8 e 3.11 (valori attesi fissati nel test).
- `ProblemSplitter`: I4 su tutti i dataset; conteggi dev/test di §10.4; stabilità al cambio di ordine dell'input.
- `line_counter`: valori di §10.3.
- `CodeExtractor`: casi con blocco con tag, senza tag, testo libero, output vuoto.
- `ThresholdCalibrator`: confronto con calcolo manuale; punteggi discreti; caso `underpowered`; FPR raggiunto ≤ target.
- Metriche: AUROC confrontato con `sklearn`; pass@1 con casi noti; bootstrap con seme fisso riproducibile; I2 (un positivo `FAILED` con punteggio alto **non** è contato come rilevato).
- `ConstrainedTprCriterion`: tie-break, nessun candidato ammissibile, segnalazione.
- Attacchi: determinismo dato il seme; sulle soluzioni canoniche di L1, `T1.*` e `T4.*` lasciano i test **verdi** (eseguiti nella sandbox, marker `apptainer`); `T1.4` non rinomina mai `entry_point`, campi o globali; `T1.5` non scrive mai su stdout.
- `InvariantObserver`: una fase che perde una riga solleva `InvariantViolation`.

### 21.4 Test oracle (fedeltà degli shim)

Per ciascun metodo, su **5 prompt fissi** di HumanEval+ con seme fisso e modello Qwen:
1. si esegue il percorso **originale** del repository (script o funzione documentata nell'audit) e si salvano output e punteggi in `tests/fixtures/oracle/<metodo>/`;
2. si esegue lo shim con gli stessi parametri;
3. **criterio:** stessi punteggi di rilevazione sullo stesso codice (tolleranza assoluta 1e-6 per z-score; uguaglianza esatta per bit e conteggi). Per la generazione, uguaglianza del testo **quando** il percorso originale usa lo stesso decoding; altrimenti si confronta solo la rilevazione su codice fissato.
4. Un test verifica che lo shim usi davvero temperature 0.2, top_p 0.95 e max_new_tokens dal request (es. ispezionando la `GenerationConfig` effettiva registrata in `extra`).

### 21.5 Smoke test end-to-end

`tests/fixtures/tiny/`: 5 problemi HumanEval (con le varianti dei 4 linguaggi), 5 MBPP+, 3 CodeNet, 20 negativi umani per linguaggio. `+experiment=smoke`: `max_new_tokens` ridotti, N = 2, bootstrap con 50 ricampionamenti, `min_negatives` ridotto. L'intera pipeline (tutti i metodi, un attacco per gruppo) deve completarsi su un nodo con una GPU e verificare I1 in ogni fase.

---

## 22. Piano di implementazione a milestone

Ogni milestone termina con test verdi, riepilogo all'utente e commit `[M<n>]`.

| # | Milestone | Contenuto | Criteri di accettazione |
|---|---|---|---|
| **M0** | Bootstrap | struttura di §4; `pyproject.toml`; pre-commit; submodule fissati (inclusi `bigcode-evaluation-harness`, `ClassEval`); confronto fork/originale di Code Acrostic (§9.4); `bench-contracts`; installazione negli ambienti; lockfile di `bench-core` | `pip check` pulito; lockfile versionati; `pytest tests/contract -m contract` verde sull'import; rapporto sul fork consegnato |
| **M1** | Nucleo | dominio, registri, `ArtifactStore` con manifest e scrittura atomica, `ExperimentBuilder`, CLI Hydra, `Pipeline`, osservatori, `bench doctor` | unit verdi; una fase fittizia scrive, salta al secondo lancio e rilancia con `force=true`; `bench doctor` produce un report leggibile |
| **M2** | Dati L1 | loader HumanEval+, MBPP+, MBPP originale, HumanEvalPack; `ProblemSplitter`; `line_counter`; negativi L1 + integrazione dev | conteggi di §10.2 per L1; statistiche righe di §10.3; I4 verificato |
| **M3** | Baseline | `PromptBuilder`, template chat, `CodeExtractor`, `HFBaselineGenerator`; fase `generate_baseline` su SLURM GPU | L1 completo per entrambi i modelli: 6 campioni per prompt, I1 verificato, tasso di estrazione riportato |
| **M4** | Sandbox ed esecuzione L1 | `sandbox.def`, build, `ApptainerSandbox`, executor `evalplus`, `humanevalpack`, `syntax`; ADR sulla rete | Pass@1 delle soluzioni canoniche = 100% (o casi anomali documentati); Pass@1 della baseline L1 per modello e linguaggio con IC |
| **M5** | Protocollo worker + primo metodo | `WorkerClient`, `ShimBase`, runner, audit e shim **STONE** (il repository più strutturato), test oracle | oracle STONE verde; ripresa dopo kill del worker verificata; I1 verificato |
| **M6** | Metodi restanti | audit e shim di SWEET, MCGMark, Code Acrostic, PromptMark, ACW (in quest'ordine); patch documentate | oracle verdi per ciascuno; audit approvati dall'utente; ACW `NOT_APPLICABLE` fuori da Python |
| **M7** | Rilevazione e calibrazione | fase `detect` (positivi, negativi umani, negativi LLM), `ThresholdCalibrator`, metriche di rilevabilità e funzionali, incertezza | soglie congelate per una configurazione di default per metodo; TPR@1%FPR, AUROC, Pass@1 e ΔPass@1 con IC su L1 dev |
| **M8** | HPO | griglie approvate dall'utente, sweep come array SLURM, `ConstrainedTprCriterion`, fase `tune` | file di configurazioni selezionate per ogni (metodo, modello, linguaggio); casi senza configurazione ammissibile segnalati |
| **M9** | Livello 2 | loader CodeNet, selezione dei 250 problemi, executor `codenet`; integrazione in calibrazione e HPO (rilanciare M7–M8 con L2 dev) | conteggi L2; Pass@1 baseline L2; HPO aggiornato |
| **M10** | Attacchi T1 | T1.1–T1.5, `ToolRunner`, `setup_tools.sh`, configurazioni dei tool | test di proprietà verdi; attacchi su L1 test con esecuzione e rilevazione post-attacco |
| **M11** | Attacchi T2–T4 | T2.1, T2.2 (Llama), T2.4, T3.1, T4.1, T4.2; T2.3 dietro flag | ogni attacco produce celle complete; T3.1 riporta le 4 categorie (k1/k2/entrambi/nessuno) |
| **M12** | Impercettibilità e capacità | perplexity StarCoder2, CodeBLEU, due classificatori avversari con CV raggruppata, metriche MCGMark | metriche con IC per L1 test; test di non-sovrapposizione dei problemi tra fold |
| **M13** | Livelli 3 e 4 | loader ClassEval, CodeSearchNet, The Stack, executor `classeval`; pilota Tests4Py su 5 progetti (se fattibile) | flag di contaminazione rispettato nel report; esito del pilota L4 documentato |
| **M14** | Report | tabelle per cella (CSV + LaTeX), figure (curve ROC, TPR vs intensità d'attacco, curva di troncamento in k), sezione efficienza con nota | report rigenerabile con un solo comando dai soli artefatti |

---

## 23. Deviazioni note dal protocollo e punti aperti

Da riportare in `docs/deviations.md` all'inizio della Milestone 0.

| ID | Descrizione | Stato |
|---|---|---|
| D1 | MCGMark: lunghezza del messaggio fissata a 24 bit invece di essere selezionata come iperparametro | decisa dall'utente |
| D2 | La "soglia di rilevamento" di ACW (e qualunque soglia nativa) non è un iperparametro: tutte le soglie di decisione si calibrano all'1% di FPR sui negativi umani di sviluppo | proposta, da confermare |
| D3 | SWEET: per i negativi senza prompt (CodeSearchNet, The Stack) l'entropia è calcolata con contesto vuoto | proposta, da confermare |
| D4 | Il codice viene estratto dall'output chat con una regola unica per tutti i metodi (§10.6) | proposta, da confermare |
| D5 | Code Acrostic: uso del repository originale; eventuale ricorso al fork solo dopo analisi dei commit | in attesa dell'esito di M0 |
| D6 | ACW applicato solo a Python | decisa dall'utente |
| D7 | Assegnazione dev/test delle soluzioni MBPP originali senza corrispettivo in MBPP+ | `TODO(user)` |
| D8 | Liste di frequenza di PromptMark calcolate da split di training disgiunti (CodeSearchNet train, campione separato di The Stack) | proposta, da confermare |
| D9 | Baseline e codice marcato condividono il seme ma girano in ambienti diversi: accoppiamento per CodeBLEU e perplexity approssimato | da dichiarare nella tesi |

**Punti aperti (`TODO(user)`):** percorsi degli interpreti, dei modelli e dei dataset; nomi delle partizioni SLURM NVIDIA e CPU; `global_seed`; griglie HPO dopo l'audit; testi dei prompt di attacco T2.2; politica di rete dei nodi di calcolo (Sourcery, `--network none`); disponibilità di `--fakeroot` per la build Apptainer; ambiente per Tests4Py.
