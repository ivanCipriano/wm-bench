# Deviazioni dal protocollo

Registro delle deviazioni dal *Protocollo Sperimentale Benchmark* (SPEC §0.4, §23).
Ogni voce ha un ID progressivo `D<n>`, motivazione, impatto e stato. Stati aggiornati con le
decisioni dell'utente in `docs/cluster_info.md` §10 (1° ottobre 2026).

| ID | Descrizione | Motivazione | Impatto | Stato |
|---|---|---|---|---|
| D1 | MCGMark: lunghezza del messaggio fissata a **24 bit** invece di essere selezionata come iperparametro | decisione dell'utente (SPEC §9.6, cluster_info §10) | la lunghezza è esclusa dalla griglia di MCGMark (SPEC §9.6); metriche di capacità su 24 bit (§13.5) | **decisa** dall'utente |
| D2 | La "soglia di rilevamento" di ACW (e qualunque soglia nativa) non è un iperparametro: tutte le soglie di decisione si calibrano all'1% di FPR sui negativi umani di sviluppo | stesso criterio di decisione per tutti i metodi | la soglia nativa resta solo diagnostica (`native_decision`) | **confermata** |
| D3 | SWEET: per i negativi senza prompt (CodeSearchNet, The Stack) l'entropia è calcolata con contesto vuoto | quei negativi non hanno un prompt di condizionamento | punteggi SWEET su questi negativi condizionati diversamente dai positivi | **confermata** |
| D4 | Il codice viene estratto dall'output chat con una regola unica per tutti i metodi (SPEC §10.6) | equità tra metodi e baseline | eventuali differenze di formato dell'output dei metodi non sono compensate | **confermata** |
| D5 | Code Acrostic: si usa il repository **originale** (`XHLin-gamer/code_acrostic` @ `83823697f01d6250e7fccd9fa7005bdcfb967ded`); dal fork si prende solo `CC.json` tramite la patch documentata `patches/acrostic/0000-aggiunge-CC-json-dal-fork.patch` | confronto originale/fork (vedi sotto) | nessuna modifica al codice del metodo | **risolta** |
| D6 | ACW applicato solo a Python | decisione dell'utente (SPEC §9.2, cluster_info §10) | celle ACW `NOT_APPLICABLE` per Java, C++ e JavaScript | **decisa** dall'utente |
| D7 | Assegnazione dev/test delle soluzioni MBPP originali senza corrispettivo in MBPP+ | nessuna fuga di informazione tra sviluppo e test | regola deterministica di cluster_info §10.1: dei 596 problemi extra, 114 in dev e 482 in test (ordinamento per `derive_seed(global_seed, "split", problem_key)`), mai usati come prompt; test di disgiunzione e totale di 1.138 negativi umani L1 Python | **decisa** (cluster_info §10.1) |
| D8 | Liste di frequenza di PromptMark calcolate da split di training disgiunti (CodeSearchNet train, campione separato di The Stack) | evitare sovrapposizione con i negativi di valutazione | liste salvate come artefatto versionato | **confermata** |
| D9 | Baseline e codice marcato condividono il seme ma girano in ambienti diversi: l'accoppiamento per CodeBLEU e perplexity è approssimato | ambienti diversi possono avere flussi RNG diversi | da dichiarare nella tesi | **da dichiarare nella tesi** |

## Rapporto sul fork di Code Acrostic (D5)

Il confronto è stato fatto dall'utente prima della Milestone 0 (cluster_info §9.1) e non è stato ripetuto:

- fork: `https://github.com/xhaughearl/code_acrostic` @ `698479462548ed44919e7eba791298f1a161cc72`, senza modifiche locali;
- `CC.ipynb` del fork è **identico** a quello dell'originale;
- il fork aggiunge solo `CC.json`, `LICENSE`, `.gitattributes`, `.gitignore`, due immagini e un README diverso;
- decisione: submodule = originale; `CC.json` aggiunto con la patch `0000`. Il ruolo di `CC.json` e di MarkLLM va chiarito nell'audit (M6).

## Differenze di implementazione rispetto a SPEC (non sono deviazioni dal protocollo)

Derivano da `docs/cluster_info.md` §1, che prevale su SPEC, e sono motivate in `docs/decisions/ADR-002-submodules-and-patched-copies.md`:

- i worker eseguono la copia patchata `build/patched/<metodo>/`, non `third_party/<repo>`;
- Python minimo dei worker 3.9 (non 3.8); nomi degli ambienti `sweet, acw, stone, code_acrostic, promptmark, mcgmark`;
- `bigcode-evaluation-harness` e `ClassEval` fissati all'HEAD del 1° ottobre 2026 (commit non indicati in cluster_info).
