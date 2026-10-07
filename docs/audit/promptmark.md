# Audit di PromptMark (SPEC §9.0)

- **Stato:** decisioni dell'utente del 7 ottobre 2026 applicate (test del ciclo dagli esempi del prompt,
  esecuzione con limiti, griglia HPO su tutti e tre i parametri). Secondo oracle (8 ottobre 2026): shim e
  percorso diretto coincidono con gli esempi eseguiti (1-2 per campione) e i limiti attivi. Oracle verde
  (4 test) dopo la correzione degli esempi con commento in coda (§8).
- **Repository:** `ahmedfahad04/promptmark`, paper "PromptMark: A Prompt-Guided Iterative-Feedback Framework
  for Source Code Watermarking" (`PromptMark_ENASE_Final_18_March.pdf` nel repository).

## 1. Commit e codice

- Commit `c04c8f61` (28 aprile 2026, submodule `third_party/PromptMark`). La patch preesistente `0000`
  tocca solo script e `requirements.txt`, non `src/`.
- Variante: **expI** (`src/watermarking/exp_iterative_wm.py`), ciclo iterativo con green list scelta per
  frequenza: è "Iterative-AGL", il metodo finale del paper (Tab. 2).
- Ambiente `promptmark` (Python 3.10.21). `patched_subdir: src` (import assoluti `shared_utils`,
  `llm_providers`); lo shim aggiunge `src/watermarking` al percorso.
- Patch nuove: `0001-inprocess-hf-provider`, `0002-parametri-soglia-green-list` (§9).

## 2. Punto d'innesto

- **Inserimento:** `run_phase1(record, max_iterations)`. A ogni iterazione:
  1. `generate_code`: prompt = istruzioni PromptMark con green e red list + template del problema
     (`##Prompt:` e `##Test Cases:`) + eventuale feedback, inviato al provider come messaggio utente;
  2. `evaluate_candidate`: esecuzione dei test del record (`test_code`) e `detect_watermark`;
  3. stop se corretto **e** p < η; altrimenti feedback: errore dei test se non corretto, altrimenti
     "usa più identificatori verdi".
- **Modello:** `generate_response` → provider globale. Patch 0001 (SPEC §9.4): `InProcessHFProvider`
  (`inprocess_hf`) con modello e tokenizer caricati dallo shim, chat con il **system prompt fisso** del
  framework e il messaggio di PromptMark come utente (`apply_chat_template`, `add_generation_prompt`),
  decoding neutro del request: `temperature=0.1` e `max_tokens=2048` passati dal codice sono ignorati.
  Il fallback silenzioso a Bedrock resta nel codice: senza credenziali né rete fallisce, e l'item è
  `FAILED`.
- **Rilevazione:** `detect_watermark("", code, green, red, γ, comment_enabled=True, z_threshold)`, come
  `evaluate_candidate` (codice originale vuoto: le statistiche "originali" non servono).
- **Configurazione dal codice:** il modulo expI legge costanti (`SEED_KEY`, `Z_THRESHOLD`, `G_MIN`,
  `G_MAX`, quest'ultime aggiunte dalla 0002) e la lista di frequenza da `base_dir="."`. Lo shim imposta
  le costanti e usa come directory corrente una cartella di lavoro con la lista del framework.

## 3. Iperparametri

| Protocollo | Codice | Paper | Default (fino all'HPO) |
|---|---|---|---|
| dimensione della green list | derivata dalla chiave: `g_min + sha256(key) % 11` con `g_min = 8`, cioè [8, 18], su un pool delle 18 iniziali più frequenti | dai primi 32 bit di SHA-256 in [G_min, G_max], pool K ∈ [10, 12] | codice (`green_size: null`) |
| iterazioni massime | `ITER_CAP = 5` | fino a 5 (Fig. 4) | 5 |
| soglia η del ciclo | `norm.sf(2.12)` ≈ 0,017, **cablata dentro `detect_watermark`** (la costante `Z_THRESHOLD` del modulo non conta) | η senza valore numerico | z = 2,12 |
| chiave | `SEED_KEY = "exp2025"` | chiave a 128 bit | `str(derive_seed(global_seed, "wm-key", "promptmark", key_id) % 2**31)` |

- **Casi della regola delle discrepanze:**
  - pool di 18 iniziali invece di 10-12, formula di |G|, permutazione con `random.Random(sha256)` invece di
    Fisher-Yates con un hash separato: **codice coerente con sé stesso ma diverso dal paper** → si segue il
    codice e si documenta (la variante del paper cambierebbe la green list, non è una misura secondaria
    economica).
  - η cablata e `g_max` ignorato (`% 11` fisso): **parametri dichiarati che il codice non rispetta** →
    patch 0002 minima, identica all'originale con i valori di default (verificata dall'oracle), necessaria
    per la griglia della M8.
- **Griglia della M8** (decisione dell'utente): iterazioni massime, η e dimensione della green list
  (`green_size` fissa `g_min = g_max`). Prima di lanciarla, stima del costo della griglia completa
  (27 configurazioni per modello, fino a 5 generazioni per campione) e, se non sta nei tempi, una griglia
  ridotta da proporre.

## 4. Liste di frequenza (D8, adattamento registrato)

- Il repository stima le frequenze delle iniziali da HumanEval e MBPP (`results/dataset/*_letter_freqs.json`):
  dati di valutazione. Caso "valori legati ai dati degli autori" → si adattano: lista Python dallo **split
  di training di CodeSearchNet** (D8 confermata), disgiunto dai negativi.
- Procedura degli autori (`scripts/utils/calculate_gamma_for_code.ipynb`): per programma gli identificatori
  **unici** di tutte le categorie di `CodeNavigator`, esclusi builtin e nomi comuni, prima lettera
  alfabetica in minuscolo; conteggi sommati. Reimplementata in `bench.data.promptmark_freq`; l'oracle la
  confronta con `CodeNavigator` del repository sui codici fissi.
- Fase CPU `promptmark_freq` → `data/promptmark_freq/python.json` (artefatto con manifest). Differenza di
  implementazione: la SPEC (§15.2) la metteva in `prepare_data`; fase a sé per non rilanciare la M2.
- Lo shim scrive la lista in `results/dataset/humaneval_letter_freqs.json` della cartella di lavoro (il
  file MBPP resta vuoto: il codice somma le due liste) e la lista entra negli iperparametri nativi, quindi
  nel `config_hash`.
- γ = somma delle frequenze delle iniziali verdi (`calculate_gamma`).

## 5. Linguaggi

- Paper: "we focus exclusively on the Python language" (§6); codice: estrazione con `ast`.
- **Caso applicato** (regola dei linguaggi): **solo Python**; Java, C++ e JavaScript `NOT_APPLICABLE`
  senza invocare il worker (D22). Nessuna lista di frequenza per gli altri linguaggi.

## 6. Punteggio di rilevazione e direzione

- Token: iniziali degli identificatori **non pubblici unici** (`CodeNavigator`: variabili, parametri, nomi
  usati, attributi non `self`, funzioni e classi con `_`; esclusi `self`, `cls`, builtin e nomi comuni) più
  la prima parola di ogni riga di commenti e docstring (`comment_enabled=True` in expI).
- p esatto `binom.sf(I − 1, N, γ)`; **punteggio = −log10 p** (`generated_score`, SPEC §9.4); più alto vuol
  dire più marcato. Decisione nativa p < η (solo diagnostica).
- Variante del paper come misura secondaria (costa poco): p adattivo dell'Eq. 5 (binomiale esatto se
  N < 30, normale altrimenti) in `extra["p_adaptive_paper"]`. Il paper (Eq. 6) chiede anche la correttezza
  per dichiarare "marcato": la rilevazione del framework vede solo il codice e non la usa.
- `FAILED`: nessun codice, errore di sintassi (nel codice il ramo d'errore non ha la chiave
  `unique_starts` e solleva `KeyError`: lo shim lo traduce) o N = 0.

## 7. Selezione del campione

- Codice: la prima iterazione corretta e marcata; se nessuna, la **prima corretta** (o la prima).
  Paper: la candidata con il miglior p. Caso "coerente ma diverso" → si segue il codice; tutte le iterazioni
  (codice, esiti degli esempi, p, risposta) sono in `extra["iterations"]`.
- `raw_output` = risposta completa della candidata scelta; il codice del campione è quello della regola D4
  applicata a quel testo, come per gli altri metodi. Il metodo valuta invece l'**ultimo** blocco `python`
  della risposta (`extract_code_from_response`), mentre D4 prende il primo: l'oracle conta i casi in cui i
  due codici differiscono. Primo oracle: coincidono in 5 campioni su 5.

## 8. Test del ciclo di correttezza (decisione dell'utente, D23)

- Il codice mette nel prompt i test del dataset (`##Test Cases`; per HumanEval le asserzioni di `check`) e
  li esegue nel ciclo: sono test di valutazione.
- Decisione: **nessun test aggiuntivo nel prompt**; il ciclo esegue solo gli esempi già presenti nel prompt
  comune a tutti i metodi (`shims/bench_shims/promptmark/examples.py`):
  - HumanEval+: esempi doctest `>>>` con uscita attesa valida come espressione →
    `assert (<sorgente>) == (<uscita>)` (confronto per valore);
  - MBPP+: righe `assert` della docstring del prompt.
- **Nessuna patch:** lo shim passa il record con `prompt` = messaggio utente del framework e `test_list` =
  esempi, senza il campo `test` (quindi niente asserzioni di `check`). La sezione `##Test Cases` del
  template del metodo ripete solo esempi già presenti nel prompt.
- Per campione si registrano iterazioni, ripetizioni per forza del watermark e per correttezza, ed esempi
  eseguiti (colonne `n_iterations`, `n_retries_watermark`, `n_retries_correctness`, `n_example_tests`).
- Sovrapposizione con i test di EvalPlus (input degli esempi presenti fra `base_input`/`plus_input`), su L1
  Python (`test_example_overlap_with_evalplus`, primo oracle):

  | Dataset | Parte | Problemi | Con esempi | Esempi | Input in base | Input in plus | Problemi con sovrapposizione |
  |---|---|---|---|---|---|---|---|
  | MBPP+ | dev | 72 | 72 | 72 | 62 | 11 | 62 |
  | MBPP+ | test | 306 | 306 | 306 | 260 | 37 | 260 |
  | HumanEval+ | dev | 24 | 10 | 30 | 23 | 10 | 9 |
  | HumanEval+ | test | 140 | 60 | 136 | 86 | 27 | 43 |

  Su MBPP+ l'assert di esempio del prompt è quasi sempre anche un test di base di EvalPlus (86% dei problemi):
  il ciclo vede un test di valutazione, ma è lo stesso che **tutti** i metodi vedono già nel prompt comune.
  Su HumanEval+ solo 70 problemi su 164 hanno esempi estraibili (gli altri li scrivono in prosa o con `➞`);
  il 64% degli input degli esempi compare fra i test di base di EvalPlus, anche qui già visibili a tutti nel
  prompt. Nessun test di valutazione **non** presente nel prompt entra nel ciclo.
- **Correzione (8 ottobre 2026):** su HumanEval+ il primo oracle trovava esempi solo in 3 problemi su 164,
  perché `doctest.DocTestParser` fallisce sull'intero testo quando la recinzione del prompt del framework
  segue la docstring. L'estrazione ora legge gli esempi riga per riga (stessa regola dell'uscita attesa di
  doctest, chiusa anche da docstring e recinzioni); nel primo oracle i 5 campioni HumanEval hanno quindi
  eseguito 0 esempi. Le iterazioni e la sovrapposizione su HumanEval+ vanno rimisurate.
- **Seconda correzione:** un esempio con commento in coda (HumanEval/32, `find_zero([1, 2]), 2)  # f(x) = 1 + 2x`)
  dava un'asserzione non valida, che nel ciclo sarebbe diventata un falso errore di correttezza: il commento
  ora si toglie (tokenizzazione Python); gli esempi senza commento restano identici.
- Secondo oracle: i 5 campioni HumanEval eseguono 1-2 esempi ciascuno, sempre superati; shim (con limiti) e
  percorso diretto (senza limiti) danno le stesse iterazioni ed esiti. Sovrapposizione su HumanEval+ dev:
  10 problemi su 24 con esempi, 30 esempi, 23 con input fra i test di base e 10 fra i plus, 9 problemi con
  sovrapposizione (tabella sopra, completa).
- **Questione aperta per la M9:** su CodeNet gli esempi di input/output del testo coincidono in gran parte
  con i test di valutazione; quando si arriva a L2 vanno proposte alternative (ciclo di correttezza
  disattivato su L2, oppure Pass@1 di PromptMark su L2 con avvertenza).

## 9. Esecuzione degli esempi (decisione dell'utente)

- Il codice esegue il codice generato in un processo figlio del worker (`multiprocessing`, timeout 2 s),
  senza sandbox. Si tiene, con limiti applicati **dallo shim** (nessuna modifica del codice): il wrapper
  di `shared_utils.run_code_with_tests`, eseguito nel figlio,
  - usa una cartella temporanea dedicata sotto `paths.tmp` (`$WMB_TMP`), fuori da repository, artefatti e
    dataset, come directory corrente, e la cancella alla fine;
  - ripulisce l'ambiente (restano `PATH` e la localizzazione; spariscono token e credenziali), nasconde la
    GPU (`CUDA_VISIBLE_DEVICES=""`), thread BLAS a 1;
  - `setrlimit`: memoria = spazio virtuale ereditato dal worker + 2 GB (il figlio di un processo CUDA
    eredita uno spazio virtuale grande), file fino a 64 MB, nessun nuovo processo o thread
    (`RLIMIT_NPROC = 0`), 10 s di CPU. Il timeout di 2 s del repository resta invariato.
- Verifica: il percorso diretto dell'oracle esegue gli esempi **senza** limiti; lo shim con i limiti deve
  dare le stesse iterazioni e gli stessi esiti.
- Rischio residuo: la **rete non è isolata**. I test di valutazione veri restano nella sandbox Apptainer
  (fase `execute`).

## 10. Prime osservazioni sull'inserimento (primo oracle, Qwen, 5 HumanEval)

- In 5 campioni su 5 il ciclo esaurisce le 5 iterazioni senza arrivare a p < 0,017 (4 ripetizioni per forza del
  watermark, codice sempre corretto) e restituisce la prima iterazione: punteggi −log10 p fra 0,2 e 0,7, come le
  soluzioni canoniche e la baseline.
- Il modello capisce le istruzioni (nella spiegazione dichiara di usare `i` e `j` come identificatori "verdi")
  ma produce lo stesso codice a ogni iterazione. Cause probabili, tutte legate al protocollo comune:
  - il prompt del framework chiede di mantenere nomi di funzione, classe e **parametri**: su HumanEval restano
    liberi solo pochi nomi locali, mentre PromptMark chiede di rinominare anche i parametri;
  - le righe della docstring copiata dal prompt contano come commenti (prima parola di ogni riga) e diluiscono
    il segnale;
  - con temperatura 0,2 le iterazioni sono quasi deterministiche (il paper usa 1,0).
- Da verificare sulla generazione di L1 (anche MBPP+, dove il modello sceglie tutti i nomi).

## 11. Decoding, semi e costo

- Decoding neutro (ADR-006); il prompt di PromptMark chiede una spiegazione dopo il codice, che può essere
  troncata da `max_new_tokens` senza toccare il blocco di codice.
- Ogni campione ha il suo ciclo (feedback diversi): un campione per item, seme della prima generazione
  `derive_seed(global_seed, "gen", modello, problema, linguaggio, i)`, delle successive
  `derive_seed(seme, "promptmark-iter", t)`; `seed_scheme: per_sample` (D9: per le metriche accoppiate
  vale il confronto a livello di problema, TODO 9).
- Costo: fino a 5 generazioni per campione; il worker riprende dagli item già scritti se il job scade.

## 12. Dipendenze ed effetti collaterali

- `shared_utils` importa `boto3`, `sklearn`, `scipy`, `pandas`; `llm_providers` importa `dotenv`: già
  nell'ambiente. Nessuna rete. Le molte `print` sono ridirette.

## 13. Equivalenza per riga

Non applicabile: PromptMark non modifica i logit (`cluster_info` §11, punto 5).

## 14. Definizioni operative degli stati

- **Embed:** `OK` se il ciclo restituisce una candidata (anche non corretta o non marcata, come il codice);
  `FAILED` su eccezione o senza candidate.
- **Detect:** `FAILED` senza identificatori, con errore di sintassi o codice vuoto; altrimenti `OK`.
