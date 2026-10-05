# Deviazioni dal protocollo

Registro delle deviazioni dal *Protocollo Sperimentale Benchmark* (SPEC §0.4, §23).
Ogni voce ha un ID progressivo `D<n>`, motivazione, impatto e stato. Stati aggiornati con le
decisioni dell'utente in `docs/cluster_info.md` §10 (1° ottobre 2026).

| ID | Descrizione | Motivazione | Impatto | Stato |
|---|---|---|---|---|
| D1 | MCGMark: lunghezza del messaggio fissata a **24 bit** invece di essere selezionata come iperparametro | decisione dell'utente (SPEC §9.5, cluster_info §10) | la lunghezza è esclusa dalla griglia di MCGMark (SPEC §9.5); metriche di capacità su 24 bit (§13.5) | **decisa** dall'utente |
| D2 | La "soglia di rilevamento" di ACW (e qualunque soglia nativa) non è un iperparametro: tutte le soglie di decisione si calibrano all'1% di FPR sui negativi umani di sviluppo | stesso criterio di decisione per tutti i metodi | la soglia nativa resta solo diagnostica (`native_decision`) | **confermata** |
| D3 | SWEET: per i negativi senza prompt (CodeSearchNet, The Stack) l'entropia è calcolata con contesto vuoto | quei negativi non hanno un prompt di condizionamento | punteggi SWEET su questi negativi condizionati diversamente dai positivi | **confermata** |
| D4 | Il codice viene estratto dall'output chat con una regola unica per tutti i metodi (SPEC §10.6) | equità tra metodi e baseline | eventuali differenze di formato dell'output dei metodi non sono compensate | **confermata** |
| D6 | ACW applicato solo a Python | decisione dell'utente (SPEC §9.2, cluster_info §10) | celle ACW `NOT_APPLICABLE` per Java, C++ e JavaScript | **decisa** dall'utente |
| D7 | Assegnazione dev/test delle soluzioni MBPP originali senza corrispettivo in MBPP+ | nessuna fuga di informazione tra sviluppo e test | regola deterministica di cluster_info §10.1: dei 596 problemi extra, 114 in dev e 482 in test (ordinamento per `derive_seed(global_seed, "split", problem_key)`), mai usati come prompt; test di disgiunzione e totale di 1.138 negativi umani L1 Python | **decisa** (cluster_info §10.1); implementata in M2 (ADR-004) |
| D8 | Liste di frequenza di PromptMark calcolate da split di training disgiunti (CodeSearchNet train, campione separato di The Stack) | evitare sovrapposizione con i negativi di valutazione | liste salvate come artefatto versionato | **confermata** |
| D9 | Baseline e codice marcato condividono il seme ma girano in ambienti diversi: l'accoppiamento per CodeBLEU e perplexity è approssimato | ambienti diversi possono avere flussi RNG diversi | da dichiarare nella tesi | **da dichiarare nella tesi** |
| D10 | Benchmark ridotto a **5 metodi**: un metodo previsto dal protocollo è escluso | decisione dell'utente (2 ottobre 2026); SPEC e cluster_info aggiornati di conseguenza | il metodo escluso, i suoi submodule, le patch e l'ambiente sono rimossi dal repository; i confronti della tesi riguardano solo i 5 metodi | **decisa** dall'utente |
| D11 | HumanEvalPack eseguito con una replica offline della logica di `bigcode-evaluation-harness` / `code_eval_octopack` invece che con l'harness stesso; tempi limite di SPEC §11.2 (Java e C++ 20 s, JavaScript 10 s, compilazione 30 s) invece di quelli dell'harness (10 s, 60 s, 10 s; `javac` 5 s) | la metrica dell'harness si scarica da Hugging Face e il modulo importa `evaluate`: non usabili offline nella sandbox (ADR-001) | stesso programma, stessi comandi e stessa regola di PASSED; i tempi diversi contano solo per programmi molto lenti o compilazioni lente sotto carico | **da confermare** dall'utente |
| D12 | Limite di memoria per campione (SPEC §11.2: `prlimit --as`) non applicato a Java e JavaScript | JVM e V8 riservano spazio di indirizzi virtuale oltre l'uso reale e con `RLIMIT_AS` basso non partono (ADR-001) | per Java e JavaScript vale il limite di memoria del job SLURM | **da confermare** dall'utente |
| D13 | Limite di memoria per campione Python a **8 GB** invece dei 4 GB di default di EvalPlus | la canonica di MBPP/255 fallisce un test plus per memoria con 4 GB (ADR-007) | campioni che usano tra 4 e 8 GB passano qui e fallirebbero con EvalPlus standard | **decisa** dall'utente (da confermare con la diagnosi a 8 GB) |
| D14 | Controllo di EvalPlus 0.3.1 corretto nel runner: (a) i test di `find_zero` (HumanEval/32) superati vengono contati; (b) un test oltre il proprio limite di tempo dà `TIMEOUT` invece di `fail`; (c) i campioni in `TIMEOUT` sono rieseguiti una volta da soli e vale la ripetizione; (d) Python con metà delle CPU del job | (a) bug di EvalPlus 0.3.1 che fa fallire ogni soluzione di HumanEval/32; (b–d) i limiti di tempo di EvalPlus dipendono dal carico del nodo (ADR-007) | HumanEval/32 valutabile; meno falsi fallimenti per tempo; primo esito e ripetizione registrati in `ExecutionRecord` | **decisa** dall'utente |
| D15 | STONE: green list sul vocabolario del generatore (`len(tokenizer)`: circa 151.665 token per Qwen2.5-Coder, 32.256 per DeepSeek-Coder) invece dei 50.272 passati da `run.py` | 50.272 è il vocabolario del modello degli autori, non un parametro del metodo; con Qwen i token con id più alto non sarebbero mai verdi (audit di STONE §7; si imposta da configurazione, senza patch) | adattamento al generatore; per DeepSeek nessuna differenza dal repository | **decisa** dall'utente |
| D16 | STONE: oltre allo z-score ufficiale (punteggio principale) si registra in `DetectionRecord.extra` lo z-score con il denominatore del paper (`z_nonsyntax`, N_E = token non sintattici); il codice ufficiale usa il numero di token sintattici (audit §5) | trasparenza su una discrepanza fra codice e paper | nessuna sul punteggio usato; misura secondaria per confrontare AUROC e TPR in M7 | **decisa** dall'utente |

## Differenze di implementazione rispetto a SPEC (non sono deviazioni dal protocollo)

Derivano da `docs/cluster_info.md` §1, che prevale su SPEC, e sono motivate in `docs/decisions/ADR-002-submodules-and-patched-copies.md` e `ADR-003-core-store-pipeline.md`:

- i worker eseguono la copia patchata `build/patched/<metodo>/`, non `third_party/<repo>`;
- Python minimo dei worker 3.9 (non 3.8); nomi degli ambienti `sweet, acw, stone, promptmark, mcgmark`;
- `bigcode-evaluation-harness` e `ClassEval` fissati all'HEAD del 1° ottobre 2026 (commit non indicati in cluster_info).
- `worker_timeout_s` = 18000 s (5 h) invece di 86400 s: i job SLURM durano al massimo 7 h su gpuq/aiq e 9 h su defq (cluster_info §1, §4);
- job CPU su `defq` senza GPU invece di una partizione CPU dedicata (`fatq` non ha QoS per l'account, cluster_info §4).
