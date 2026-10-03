# ADR-001 — Sandbox dei test: immagine, rete, formato, esecuzione

- **Stato:** accettata (Milestone 4, 3 ottobre 2026). L'esito sui nodi `tnode` (defq) va aggiunto dopo
  `scripts/check_sandbox.sh` (sezione *Verifica sul cluster*).
- **Contesto:** SPEC §11; cluster_info §5 (Apptainer 1.1.9 via modulo, `--fakeroot` e `--network none`
  funzionanti sul nodo di login, **niente squashfuse**), §6 (i nodi di calcolo hanno internet).

## Decisioni

1. **Immagine** `containers/sandbox.def` su `python:3.11-slim-bookworm`.
   - Contiene: EvalPlus 0.3.1 (stessa versione di bench-core); g++ con `libssl-dev` e `libboost-dev`, perché i test
     C++ di HumanEvalPack includono `boost/any.hpp` (problema 22) e `openssl/md5.h` (162); OpenJDK 17; Node.js con
     `js-md5` 0.8.3 (richiesto dal problema JavaScript 162); il runner del progetto in `/opt/wmb/`.
   - Le versioni effettive (gcc, javac, node, js-md5, evalplus, hash di `pip freeze`) sono scritte dalla build in
     `/opt/wmb/versions.json` e copiate nel manifest di ogni artefatto (`sandbox_versions`).
   - Build con `scripts/build_sandbox.sh` sul nodo di login (`--fakeroot`); immagini in `$WMB/containers/`, fuori
     dal repository.

2. **Formato directory.** Senza squashfuse ogni `apptainer exec` su un `.sif` lo riconverte in una sandbox
   temporanea. Si esegue quindi sulla directory `sandbox_dir/` derivata **dal `.sif`**
   (`apptainer build --sandbox`). In ogni `ExecutionRecord` e nel manifest va lo SHA-256 del `.sif`
   (`sandbox.sif.sha256`); il doctor verifica che il `.sif` corrisponda all'hash.

3. **Un'invocazione del container per problema.** Le soluzioni di un problema (fino a 6) girano in un solo
   `apptainer exec`. Dentro, il runner esegue ogni campione in un **sottoprocesso separato**, nella propria
   cartella, con timeout (gruppo di processi ucciso alla scadenza) e limite di memoria.
   - Perché: un avvio del container ogni 6 campioni invece di uno per campione; la ground truth di EvalPlus si
     carica una volta per problema. È la seconda strada indicata da cluster_info §5, combinata con la prima.
   - Isolamento: i campioni dello stesso problema condividono il container ma non la cartella; tra problemi
     diversi l'isolamento è completo.
   - Un fallimento del container (non del codice) dà `SANDBOX_ERROR`, mai un'eccezione: nessun campione sparisce (I1).

4. **Rete: `--net --network none` sempre.** Funziona da utente non privilegiato sul nodo di login (cluster_info §5);
   il doctor e `tests/integration/test_sandbox_apptainer.py` verificano che dal container non si apra una
   connessione. Le opzioni restanti sono quelle di SPEC §11.2: `--containall --cleanenv --no-home`, più
   `--no-mount bind-paths` (i bind path di `apptainer.conf`, es. `/mnt/beegfs`, resterebbero visibili anche con
   `--containall`: il codice generato non deve poter leggere o scrivere gli artefatti); sola
   `/work` montata in scrittura, `timeout --kill-after=5` sull'intera invocazione.

5. **Memoria.** Il limite di SPEC §11.2 (`prlimit --as`) si applica **per campione** dentro il runner:
   - Python: 4 GB, passato a EvalPlus (`EVALPLUS_MAX_MEMORY_BYTES`, suo valore di default);
   - C++: 4 GB;
   - Java e JavaScript: nessun limite per processo. JVM e V8 riservano spazio di indirizzi virtuale ben oltre l'uso
     reale e con `RLIMIT_AS` basso non partono. Il limite resta quello del job SLURM (cgroup).

6. **EvalPlus (HumanEval+, MBPP+).**
   - **Ground truth** calcolata una volta, nella sandbox, con `evalplus.evaluate.get_groundtruth` sui file JSONL
     fissati (cluster_info §8.1); fase `evalplus_groundtruth`, artefatto `execution/_groundtruth/evalplus.parquet`
     con il pickle prodotto da EvalPlus per ogni problema. È ciò che EvalPlus fa con la sua cache.
   - **Esecuzione** con `evalplus.eval.untrusted_check`, prima test base poi plus, come
     `evalplus.evaluate.check_correctness`, con `fast_check` come nel default di `evaluate`; `min_time_limit=1`
     e `gt_time_limit_factor=4` (default di EvalPlus 0.3.1).
   - PASSED solo se passano base **e** plus (SPEC §11.3). EvalPlus non distingue un errore a runtime da una
     risposta sbagliata: entrambi sono `FAILED`. Il runner aggiunge solo `SYNTAX_ERROR` (il codice non compila
     con `compile`). `n_tests` è il numero di input base+plus; `n_passed` vale solo per i PASSED (con
     `fast_check` il conteggio parziale non è affidabile).

7. **HumanEvalPack (Java, C++, JavaScript): replica offline dell'harness.**
   - L'harness (`bigcode_eval/tasks/humanevalpack.py`) importa `evaluate` e chiama la metrica
     `Muennighoff/code_eval_octopack`, scaricata da Hugging Face: nessuno dei due è usabile offline nella sandbox.
     Si replica la stessa sequenza (`bench.execution.harness`):
     - preparazione delle generazioni come in `process_results`: C++ con `IMPORT_HELPER["cpp"]` davanti e il codice
       tagliato a `int main`; Java senza `public class Main {\n    }`; JavaScript invariato;
     - programma = generazione preparata + `"\n"` + `test` del dataset (`code_eval_octopack`);
     - comandi di `code_eval_octopack/execute.py`: `g++ -std=c++11 test.cpp -lcrypto -lssl` e `./a.out`;
       `javac Main.java` e `java -cp . Main`; `node test.js`;
     - esito PASSED con la stessa regola: C++ e Java con codice di uscita 0; JavaScript se non scrive nulla né su
       stderr né su stdout (i test usano `console.assert`, che non cambia il codice di uscita).
   - `IMPORT_HELPER` si legge dal sorgente del submodule con `ast`, senza copiarlo. I test
     (`tests/unit/test_harness.py`) verificano che le righe replicate siano ancora nel submodule.
   - Gli stati di fallimento distinguono il motivo (`COMPILE_ERROR`, `FAILED` se il messaggio contiene
     un'asserzione, `RUNTIME_ERROR`, `SYNTAX_ERROR` per JavaScript, `TIMEOUT`). L'insieme dei PASSED coincide con
     quello dell'harness.
   - **Tempi:** SPEC §11.2 (Java e C++ 20 s, JavaScript 10 s per l'esecuzione), più 30 s per la compilazione.
     L'harness usava 10 s (Java), 60 s (C++), 10 s (JS) e 5 s per `javac`: le differenze contano solo per
     programmi lentissimi o compilazioni lente sotto carico. Sono registrate in `docs/deviations.md`.

8. **`syntax`** (dati senza test, L3/L4):
   - prima tree-sitter sull'host (un nodo ERROR dà `SYNTAX_ERROR`);
   - poi, nella sandbox, `py_compile` o `node --check` (che danno `SYNTAX_ERROR`), oppure `javac` o
     `g++ -fsyntax-only` (che danno `COMPILE_ERROR`);
   - i metodi Java isolati vengono racchiusi in una classe;
   - da riverificare con i dati reali in M8.

9. **Fase `execute`.**
   - Celle `source=canonical`: (livello, linguaggio), tutti i problemi, verifica dell'executor.
   - Celle `source=llm_baseline`: (modello, livello, linguaggio, parte).
   - Problemi in parallelo su `SLURM_CPUS_PER_TASK` thread: il lavoro è nei sottoprocessi della sandbox.
   - Ripresa con file parziale JSONL, come la baseline.
   - I job CPU (`slurm_cpu`, defq) caricano il modulo Apptainer tramite il campo `setup` del profilo.

## Conseguenze

- Un cambiamento del runner richiede di ricostruire l'immagine, quindi cambia l'hash registrato. È voluto: la
  provenienza copre anche il codice che esegue i test.
- La ground truth di EvalPlus dipende dai tempi misurati sul nodo che la calcola, come in EvalPlus: i limiti di
  tempo (≥ 1 s per test) lasciano ampio margine.
- Le canoniche che non passano vanno in `configs/execution/known_anomalies.yaml` con il motivo, e in
  `docs/deviations.md` se dipendono dal dataset.

## Verifica sul cluster

Da compilare dopo `scripts/check_sandbox.sh` (nodo defq): rete assente, isolamento, versioni, canoniche di
prova; tempo di avvio di un `exec` sulla directory.
