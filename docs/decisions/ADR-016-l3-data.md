# ADR-016 — Dati ed esecuzione del Livello 3 (Milestone 13)

- **Stato:** in corso (Milestone 13). Decisioni dell'utente del 29 ottobre 2026.
- **Contesto:**
  - SPEC §10.1–10.2 (loader `classeval`, `codesearchnet`, `thestack_cpp`; 94 classi dopo le
    esclusioni, 500 funzioni per linguaggio), §10.4 (L3 solo test), §11.3 (executor `classeval` e
    `syntax`), I6 (contaminazione), §22 (M13);
  - TODO 30 (6 classi di ClassEval escluse, D21), TODO 34–35 (pilota e prompt d'esempio prima della
    generazione), D39 (CodeSearchNet senza le funzioni già usate in L1), criteri di fattibilità di
    Tests4Py in `cluster_info.md` §8.7.

## 1. ClassEval

**Loader** (`bench/data/loaders/classeval.py`): `third_party/ClassEval/data/ClassEval_data.json`
(100 classi) meno le 6 di `configs/dataset/classeval_excluded.yaml`, con `check_not_excluded`
come per CodeNet: 94 classi, tutte di test (famiglia `classeval: {dev: 0, total: 94}`).

- prompt = **scheletro ufficiale** (`skeleton`: import, descrizione della classe, costruttore,
  firme e docstring dei metodi), come nella generazione olistica di ClassEval;
- `entry_point` = nome della classe; `test_ref` = `task_id`; canonica = `solution_code`;
- `loc_to_generate` = righe di codice (SPEC §10.3) della canonica meno quelle dello scheletro;
- negativo umano = la classe canonica (94 negativi), `contamination_risk = False`.

**Template** (`configs/prompt/user/classeval_python.j2`), con la stessa forma di quelli di L1 e
l'istruzione dell'originale ("Please complete the class {class_name} in the following code"):

```text
Complete the class `{{ entry_point }}` in the following Python code.
Implement every method whose body is missing, following its docstring. Keep exactly the names and signatures given in the code (class, method and parameter names): the tests call `{{ entry_point }}` and its methods by name.
Return the complete class, including the given imports, in a single ```python code block.

```python
{{ prompt }}```
```

Decoding `classeval` (D40): come `level234` (6 campioni, temperatura 0,2, top-p 0,95) ma con
**2.048** token nuovi, uguale per la baseline e per tutti i metodi; system prompt ed estrazione del
codice uguali agli altri livelli (D4). Nella cella L3 Python (ClassEval e CodeSearchNet) la
baseline usa un backend per decoding e i metodi un'invocazione del worker per gruppo
(`decoding_overrides`, `embed_from_prompts(..., decoding_by_dataset=...)`), senza cambiare il
protocollo dei worker; per L1 e L2 richieste, impronte e manifest restano identici.

**Executor `classeval`** (`bench/execution/executors.py`, `bench/execution/classeval_check.py`):
segue `classeval_evaluation/test_pipeline.py`:

- programma = `import_statement` della classe + `add_static_statement(codice)` + `"\n"` + `test`
  (`gen_code_list`, `gen_py_file`); `add_static_statement` è verificata contro il sorgente del
  submodule (`test_add_static_statement_matches_classeval`);
- ogni classe di test gira con `unittest` entro **5 s** (`func_set_timeout(5)`);
- PASSED solo se ogni classe di test esegue almeno un test senza errori né fallimenti (`success`
  di `get_test_answer`); FAILED se un test fallisce, va in errore o in timeout; RUNTIME_ERROR se il
  modulo non si importa; SYNTAX_ERROR da `py_compile`;
- l'estrazione del codice è quella del benchmark (D4), non `extract_code` di ClassEval.

**Correzioni del dataset** (uguali per tutti i metodi, decisione dell'utente del 29 ottobre 2026
per la seconda):

- ClassEval_97 elenca la classe di test `" Words2NumbersTestMain"` con uno spazio iniziale, che
  `getattr` non trova (anche l'harness originale la conta come errore): il loader toglie gli spazi
  agli estremi dei nomi delle classi di test;
- ClassEval_69 elenca `TestPDFHandler`, che ha solo `setUpClass` e `tearDownClass` (appoggio per le
  altre classi): con 0 test l'harness la conta come errore, quindi la classe non si potrebbe mai
  risolvere, nemmeno con la canonica. Il loader toglie le classi di test senza metodi `test*`
  (`test_methods`); è l'unica classe di ClassEval toccata da questa regola.

**Esclusioni da Pass@1** (D41): ClassEval_17 (data corrente), 48 (rete) e 58 (mappa casuale) restano
per rilevazione e impercettibilità ma non entrano in Pass@1, ΔPass@1 e nella preservazione
condizionata di ACW (`configs/dataset/classeval_pass1_excluded.yaml`, letto da `metrics`): Pass@1
di ClassEval è su 91 classi.

**Immagine `sandbox_l3`** (`containers/sandbox_l3.def`, `bash scripts/build_sandbox.sh l3`): è
l'immagine di L1/L2 con in più i pacchetti importati dalle classi e dai test di ClassEval (versioni
con ruote per Python 3.11: numpy 1.26.4, scipy 1.11.4, pandas 1.5.3, gensim 4.3.2,
beautifulsoup4 4.12.3, python-docx 1.1.2, openpyxl 3.0.10, pillow 9.5.0, nltk 3.8.1,
reportlab 4.0.4, PyPDF2 3.0.1) e i dati di NLTK di ClassEval_52 (punkt, wordnet, omw-1.4,
averaged_perceptron_tagger), che senza rete non si possono scaricare. L'immagine di L1/L2 non
cambia, così i suoi risultati restano riproducibili; le celle di L3 usano `sandbox_l3`.

**Pilota** (TODO 34, `scripts/pilot_classeval.{py,sh}`): 10 classi (ordine
`derive_seed(global_seed, "classeval-pilot", problem_key)`), Qwen e DeepSeek, generazione identica
a quella della baseline, con il numero di token generati; test ufficiali nell'immagine
`sandbox_l3`, e in più le canoniche di tutte le 94 classi (3 ripetizioni, come
`canonical_repeats`). **Regola dell'utente:** se più del 5% delle risposte è troncato dal limite
di 1.024 token ci si ferma e si propone una soluzione prima del lancio.

Prova locale (Windows, senza parte dei pacchetti di terze parti): 85 canoniche su 94 passano dopo
la correzione di ClassEval_97; 6 mancano solo dei pacchetti (34, 38, 44, 52, 69, 93); 3 falliscono
un test (17 `CalendarTestGetUpcomingEvents`, che dipende dalla data corrente; 48
`IpUtilTestGetHostname`, che dipende dalla rete; 51 `KappaCalculatorTestFleissKappa`, causa da
capire); 58 (mappa casuale di Minesweeper) è fallita in una prova su due. Vanno riverificate nella
sandbox con 3 ripetizioni: le canoniche che non passano diventano anomalie documentate
(`execution.anomalies`, criterio di M4).

**Primo pilota** (`M13_classeval_pilot.txt`, 1.024 token): troncato il **7,5%** delle risposte
(Qwen 6/60, tutte su ClassEval_42; DeepSeek 3/60, su ClassEval_21), sopra la soglia del 5%: ci si è
fermati. Estrazione riuscita al 100%; campioni che passano i test 35% (Qwen) e 45% (DeepSeek);
canoniche che passano in ogni ripetizione 90/94 (17, 48, 58, 69). La diagnosi
(`M13_classeval_length.txt`, tokenizer dei due modelli) conferma che le risposte troncate sono
classi lunghe ancora in scrittura, non ripetizioni (i modelli riscrivono le docstring dello
scheletro: 54/54 e 48/57 dei campioni non troncati); la risposta ideale supera 1.024 token in 18
(Qwen) e 36 (DeepSeek) classi su 94 e 2.048 solo in ClassEval_17 con DeepSeek (2.051 token, esclusa
da Pass@1). Decisione dell'utente: 2.048 token solo per ClassEval (D40) e nuovo pilota sulle stesse
10 classi.

**Secondo pilota** (2.048 token, `M13_classeval_pilot_2048.txt`): **0/120 risposte troncate**
(regola rispettata); token nuovi al più 1.255 (Qwen) e 1.066 (DeepSeek). Stessi semi: i campioni
che prima non erano troncati sono identici; le risposte prima troncate ora si chiudono (ClassEval_42
con Qwen da SYNTAX_ERROR a FAILED, ClassEval_21 con DeepSeek). Campioni che passano i test 35%
(Qwen) e 45% (DeepSeek), classi con almeno un campione corretto 5/10 e 6/10. Canoniche che passano
in ogni ripetizione **92/94**: ClassEval_69 passa con la correzione delle classi di test senza test;
falliscono sempre 17 e 48; 58 questa volta passa 3/3 ma dipende dal caso (1/3 nel primo pilota) e
resta esclusa da Pass@1 (D41). Via libera alla generazione di ClassEval.

## 2. CodeSearchNet e The Stack C++

Decisioni dell'utente del 29 ottobre 2026: prompt firma + docstring (CodeSearchNet) e commento di
documentazione + firma (The Stack), 500 funzioni per linguaggio stratificate per righe, executor
`syntax`, niente Pass@1 (I6); commenti di The Stack filtrati come le raccolte degli attacchi;
esclusione delle funzioni già usate in L1 (D39). Prima della generazione l'utente vede un esempio di
prompt per linguaggio (TODO 35, `scripts/preview_l3_prompts.py`).

**CodeSearchNet** (`bench/data/loaders/codesearchnet.py`, split di **test**):

- *pool dei prompt*: funzioni con `func_documentation_string` non vuota da cui si costruisce il
  prompt (`bench/data/l3_prompts.py`):
  - Python: il codice fino alla fine della docstring (prima istruzione del corpo); senza docstring
    nel codice la funzione si scarta (`no_doc_in_code`);
  - Java e JavaScript: il commento di documentazione del sorgente se `whole_func_string` lo
    contiene (`doc_kind = source`), altrimenti ricostruito da `func_documentation_string` come
    `/** ... */` (`built`), poi la firma fino alla graffa del corpo compresa;
  - scarti contati: `no_documentation`, `parse_error`, `no_name` (funzione senza nome per
    tree-sitter, per esempio una proprietà `foo: function(...)`), `empty_body`;
- *negativi*: tutte le funzioni dello split di test che si analizzano (come l'integrazione di L1);
- **D39**: le funzioni già negativi di integrazione del test L1 (Java, JavaScript) si tolgono dal
  pool e dai negativi, con i conteggi nel manifest (`d39_*`);
- 500 prompt per linguaggio stratificati per decili delle righe del corpo del pool
  (`derive_seed(global_seed, "sample", "codesearchnet", lang, "l3_prompts")`).

**The Stack C++** (`bench/data/loaders/thestack_cpp.py`, repository del bucket `l3_test`):

- *pool dei prompt*: funzioni più esterne con un blocco di commenti contiguo subito sopra (o sopra il
  loro `template`), che passa i filtri sul testo pulito dell'intero blocco: lunghezza 10-2.000
  caratteri, poi `license`, `generated`, `directive`, `code`, `non_english` di ADR-012 (stesse
  espressioni regolari); scartate anche le funzioni generate o minificate (riga oltre 200 caratteri
  o marcatori) e quelle con corpo vuoto. Si accettano commenti Doxygen (`/**`, `/*!`, `///`, `//!`)
  e normali (`//`, `/* */`), contati a parte (`doc_kind`);
- prompt = commento + firma fino alla graffa del corpo; canonica = commento + funzione;
- *negativi*: le 500 funzioni dei prompt + 9.500 altre funzioni del bucket, stratificate per decili
  delle righe di tutte le funzioni del bucket (10.000, SPEC §10.2);
- `problem_key = thestack/<hexsha>:<riga>`, la stessa chiave del negativo della funzione.

**Template** (`configs/prompt/user/{codesearchnet_python,codesearchnet_java,
codesearchnet_javascript,thestack_cpp_cpp}.j2`), per esempio Java:

```text
Complete the following Java method.
Implement the body of the method following its documentation comment. Keep exactly the method name and signature given in the code.
Return the complete method in a single ```java code block.

```java
{{ prompt }}```
```

Python dice "function" e "docstring", JavaScript e C++ "function" e "documentation comment".
Decoding `level234` (1.024 token). L'estrazione (D4) tratta CodeSearchNet e The Stack come
HumanEval+ e HumanEvalPack: se il codice estratto non definisce l'`entry_point` si antepone il
prompt (il modello ha restituito solo il corpo).

**Esecuzione:** `prepare_data` con `levels=[L3]` (`bash scripts/submit_l3.sh data`, un job CPU)
scrive `data/splits/split_L3.parquet`, `data/problems/L3_<lang>.parquet` (Python: ClassEval +
CodeSearchNet) e `data/negatives/L3_<lang>_native.parquet`; poi
`python scripts/preview_l3_prompts.py` sul nodo di login.

**Esito** (`M13_l3_prompts.txt`): split L3 = 94 classi + 1.500 funzioni di CodeSearchNet + 500 di
The Stack, tutte di test.

| linguaggio | righe | pool dei prompt (scarti principali) | D39 (prompt / negativi) | prompt | negativi |
|---|---|---|---|---|---|
| Python | 22.176 | 22.147 (25 senza docstring nel codice, 3 non analizzabili, 1 corpo vuoto) | 0 / 0 | 500 + 94 ClassEval | 22.173 + 94 |
| Java | 26.909 | 26.881 (28 non analizzabili) | 788 / 788 | 500 | 26.093 |
| JavaScript | 6.483 | 4.420 (2.042 senza nome, 21 non analizzabili) | 546 / 788 | 500 | 5.674 |
| C++ | 45.794 file, 462.756 funzioni | 30.630 (12.601 Doxygen, 18.029 normali) | — | 500 | 10.000 |

- Java: `whole_func_string` non contiene mai il Javadoc (`doc_kind = built` per tutte), quindi il
  commento del prompt è sempre ricostruito da `func_documentation_string`; per JavaScript lo
  stesso (4.420 `built`).
- JavaScript: il 32% delle funzioni non ha nome per tree-sitter (proprietà `foo: function`,
  funzioni anonime) e non entra nel pool; D39 toglie dal pool 546 delle 788 funzioni di L1 (le altre
  erano già senza nome).
- C++: scarti dei filtri sul commento: non inglese 11.851, lunghezza 4.444, generati 3.462, codice
  commentato 3.440, direttive 281, licenze 30 (gli altri: 366.842 senza commento sopra, 32.392 con
  errori di sintassi, 4.938 senza nome, 2.229 a corpo vuoto, 2.217 generate o minificate).
- Righe del corpo dei 500 prompt (media ± dev. st.): Python 13,6 ± 16,2, Java 13,5 ± 18,5,
  JavaScript 21,9 ± 42,9, C++ 16,4 ± 21,7; nessuno spostamento fra decili.
- In CodeSearchNet Python i metodi hanno la riga `def` senza indentazione e il corpo con quella
  originale (come nel dataset): i prompt la conservano.
- Le funzioni C++ e i metodi Java usano spesso tipi e membri del loro progetto: la compilazione
  dell'executor `syntax` (`g++ -fsyntax-only`, `javac`) fallirà spesso anche per codice corretto.
  Per I6 Pass@1 non si riporta; lo stato `syntax` resta informativo.

## 3. Tests4Py (L4)

Dopo tutto L3; criteri di non fattibilità fissati in `cluster_info.md` §8.7.
