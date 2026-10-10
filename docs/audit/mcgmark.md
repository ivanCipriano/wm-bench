# Audit di MCGMark (SPEC §9.0)

- **Stato:** decisioni dell'utente del 6 e 7 ottobre 2026 (messaggio di 12 bit, γ fisso, seme per campione,
  solo Python, prefill, baseline gemella, L1 senza cicli completi, verifica su codice lungo, patch 0003)
  applicate. Oracle verde il 7 ottobre 2026 (10 test, codice lungo compreso); §5.3 chiuso.
- **Repository:** `KevinHeiwa/MCGMT`, paper "MCGMark: An Encodable and Robust Online Watermark for Tracing
  LLM-Generated Malicious Code" (arXiv 2408.01354v2).

## 1. Commit e data

- Commit `eefa27b6` (submodule `third_party/MCGMT`).
- Il codice eseguito è la copia `build/patched/mcgmark/` (ADR-002) con quattro patch:
  - `0000` (preesistente, dell'utente): import riparati, moduli `homoglyphs`/`normalizers` copiati da
    lm-watermarking, simboli mancanti di `watermark_global.py` ricostruiti (§3.2);
  - `0001` (nuova): γ = 0,5 fisso (D18);
  - `0002` (nuova): rimossa la decodifica inutile dell'intero vocabolario a ogni passo;
  - `0003` (nuova): rimossi i calcoli per passo diventati inutili dopo la 0001 (solo prestazioni, §9).
- Ambiente `mcgmark`: Python 3.10.21 (dal log di `install_contracts.sh`). Versioni di torch e transformers registrate dall'introspezione del worker.
- Il codice si importa da `Watermark/` con import assoluti: `patched_subdir: Watermark`.

## 2. Punto d'innesto

- **Inserimento:** `watermark_processor.py::WatermarkLogitsProcessor` (estende `WatermarkBase`), costruito come in
  `Watermark/watermark.py:225-234`: vocabolario ordinato per id, `gamma`, `delta`, `seeding_scheme`,
  `select_green_tokens`, `tokenizer`. Lo shim aggiunge la chiave del framework (`hash_key`).
  - A ogni passo il processor decodifica l'ultimo token e fa avanzare una **macchina a stati** sintattica
    (`_bias_greenlist_logits`, righe 359-895): i token dopo `def`, `class`, `print`, `for`, `while`, `=`, `#`, i
    confronti, le parentesi aperte, le stringhe e le docstring bloccano l'inserimento fino alla chiusura; gli spazi
    annullano la posizione appena usata.
  - Sulle posizioni idonee (dopo i primi 4 passi) inserisce un bit: green list ± bias.
  - **Prefill della fence (D20).** Il turno dell'assistente inizia con `` ```python`` + a capo, nel prompt (`hparams.assistant_prefill`). Senza prefill la risposta chat si apre con `` ``` ``, che `case_4` tratta come una docstring: l'inserimento resta bloccato fino alla fence di chiusura, cioè per tutto il codice (primo oracle, 6 ottobre 2026: 0 posizioni marcate su 5 campioni, tutti `PARTIAL`). Gli autori generavano **in completamento** (`watermark.py`: prompt come testo semplice, nessuna fence); il prefill riproduce quella condizione: il codice generato parte subito e coincide con quello che estrae la regola D4. `raw_output` contiene prefill + testo generato e l'estrazione D4 si applica a quel testo, come per gli altri metodi.
  - **Baseline gemella (D20).** Fase `generate_baseline_twin`: stesso shim con `hparams.watermark = False` (nessun processor), stesso prompt e prefill, stessi semi per campione. Output `baseline/<modello>/twin/mcgmark/<config_hash>/...`, campioni `llm_baseline` con `method = mcgmark`. È la baseline di riferimento di MCGMark per ΔPass@1, CodeBLEU, ΔPPL e classificatore avversario; il confronto con la baseline normale è secondario.
  - Unica differenza fra generazione e rilevazione: in generazione la macchina a stati vede per primo l'ultimo token del prefill (a capo), in rilevazione parte dal primo token del codice estratto. `test_extraction_from_code_recovers_the_embedding` verifica sul codice estratto con D4 che posizioni e bit del primo ciclo coincidano con quelli registrati in generazione.
  - Le posizioni idonee si raggruppano in **cicli di 24**: 12 bit di informazione (il messaggio) e 12 di correzione.
- **Rilevazione:** il repository non ha una rilevazione su codice arbitrario che restituisca il messaggio:
  - `WatermarkDetector.detect` rigioca la macchina a stati (`_pseudo_generate_mask`), stampa un verdetto e restituisce
    `{}`;
  - l'estrazione dei bit esiste solo dentro `__call__`, al passo `TOKEN_LENGTH`, sulle posizioni registrate in
    generazione (`First_watermark_token` + `detect(..., result_detection=True)` + `detection_result`).
  - Lo shim **compone le due parti del repository**, senza codice nuovo di logica: posizioni ricostruite da
    `_pseudo_generate_mask` sul solo codice, bit da `WatermarkLogitsProcessor.detect` sulle stesse posizioni, poi
    `informazione XOR correzione` per ciclo, come `detection_result`. L'oracle verifica che l'estrazione dal codice
    ritrovi le posizioni e i bit registrati in generazione.

### 2.1 Siti idonei: unità e capacità

- **Unità: il token generato.** Un sito è un passo di generazione in cui la macchina a stati consente
  l'inserimento: riceve un bit e il token prodotto a quel passo lo porta. Lo si vede dai token registrati in
  `First_watermark_token`, consecutivi anche sulla stessa riga (es. `class`, ` for`, ` number`, ` in`,
  ` number`, `_set`, `:\n` per `for number in number_set:`). I blocchi (`=`, `#`, parentesi, stringhe, …)
  escludono il resto della riga o del costrutto, per questo i siti sono pochi rispetto ai token.
- Ogni sito riceve un bit, quindi **siti idonei = posizioni marcate**. Per ogni campione marcato il numero è
  salvato nella colonna `n_sites` della tabella `watermarked/...` (con `n_generated_tokens`); il manifest della
  cella riporta media, mediana, minimo e massimo (`n_sites`) e il tasso di inserimento riuscito
  (`embed_success_rate` = OK / (OK + PARTIAL + FAILED)). Sono i dati "siti idonei" da riportare per livello.
- **L1** (decisione dell'utente del 7 ottobre 2026): i campioni senza un ciclo completo restano `PARTIAL` e la
  rilevazione `FAILED` con punteggio minimo, come prevede I2; nessuna nuova deviazione. Nell'oracle (5 prompt)
  nessun campione arrivava a 24 siti; la generazione di L1 dev (8 ottobre 2026) mostra che **pochi** ci
  arrivano:

  | Modello | OK / campioni | Inserimento riuscito | Siti: media, mediana, min-max |
  |---|---|---|---|
  | Qwen2.5-Coder 7B | 46 / 576 | 8,0% | 10,7; 7; 1-116 |
  | DeepSeek-Coder 6.7B | 10 / 576 | 1,7% | 7,8; 6; 0-151 |

  TPR e tasso di inserimento su L1 sono quindi **molto bassi ma non nulli** (promemoria per la M7). I campioni
  con molti siti sono probabilmente generazioni degenerate in ripetizioni (da verificare in M7 insieme al
  Pass@1). La scelta degli iperparametri di MCGMark si baserà soprattutto sulla parte di sviluppo di L2 Python
  (M8, da dichiarare nella tesi).
- **Verifica su codice lungo** (decisione dell'utente): l'oracle genera anche su 3 problemi CodeNet (esclusi
  dalla selezione della M9, `configs/dataset/codenet_excluded.yaml`) e 6 classi ClassEval, con
  `max_new_tokens` 1024 e template provvisori solo per l'oracle (`tests/oracle/templates/`), e riporta per
  campione siti, ciclo completo e bit recuperati. Questi campioni non entrano in nessuna metrica né nella scelta
  degli iperparametri. Risultati (oracle del 7 ottobre 2026):

  | Campione | Siti | Ciclo di 24 | Bit recuperati (codice D4) |
  |---|---|---|---|
  | codenet/p02749 | 12 | no | — |
  | codenet/p03553 | 19 | no | — |
  | codenet/p00200 | 9 | no | — |
  | classeval/ClassEval_73 | 20 | no | — |
  | classeval/ClassEval_14 | 40 | sì | 12/12 |
  | classeval/ClassEval_82 | 30 | sì | 12/12 |
  | classeval/ClassEval_90 | 26 | sì | 12/12 |
  | classeval/ClassEval_18 | 20 | no | — |
  | classeval/ClassEval_64 | 81 | sì (3 cicli, tutti uguali al messaggio) | 12/12 |

  - Inserimento riuscito in 4 campioni su 9; in tutti e 4 il messaggio si recupera per intero dal codice estratto
    con D4, con posizioni allineate alla generazione. **La catena funziona.**
  - I campioni senza ciclo completo hanno poche posizioni perché la generazione degenera in ripetizioni dentro un
    costrutto bloccato (righe di commento `#print(...)` ripetute, `self.exp = ... % ...` ripetuto fino a
    1024 token). Lo si vede anche su L1 (`returnFalse`, `def __ init __`, ripetizioni fino a 512 token): il bias
    pari all'intero scarto dei logit forza scelte innaturali. Effetto atteso su ΔPass@1.
  - **Conferma in M7 (L1 dev, 10 ottobre 2026; `docs/milestone_logs/M7_check.txt`,
    `M7_mcgmark_errors.txt`).** Pass@1 marcati contro gemella: Qwen 27% contro 76%, DeepSeek 31% contro 65%.
    Campioni marcati con errore di sintassi: 182 su 576 (Qwen) e 117 su 576 (DeepSeek), contro 0 nella gemella
    con lo stesso seme, prompt e prefill. Gli errori di sintassi hanno in media più posizioni marcate di quelli
    che passano (Qwen 12,4 contro 7,6). Nei campioni ispezionati, alle posizioni marcate spariscono o si
    sostituiscono token obbligati (`for i range(...)`, `for num` senza `in arr:`, `file_name split('.')`,
    `returnlargest_negative`), compaiono caratteri invisibili (U+200B) e la generazione si interrompe a metà
    (docstring troncata). Meccanismo: con bit 0 tutta la green list riceve `−(max − min)` dei logit, quindi se
    il token più probabile è verde si sceglie il miglior token rosso di una metà casuale del vocabolario. Quel
    vocabolario comprende anche token speciali ed EOS (`tokenizer.get_vocab()`, come `watermark.py:225-228`):
    un token speciale sparisce dal testo decodificato, EOS chiude la risposta. Con bit 1 il codice protegge il
    token più probabile (`max_index` aggiunto alla green list), con bit 0 no. È il comportamento del codice
    degli autori, coerente con sé stesso: lo si segue senza patch (regola delle discrepanze) e il calo di
    Pass@1 è un risultato da riportare.
  - Soluzioni canoniche di ClassEval (codice umano): 43-74 siti, punteggi 5-8 su 12, compatibili con
    Bin(12, ½) (§5.2).

## 3. Iperparametri e messaggio

### 3.1 Iperparametri

| Protocollo | Codice | Paper | Framework |
|---|---|---|---|
| frazione della green list γ | ricalcolata a ogni passo: 0,25 se i logit sono "uniformi" (std ≤ 0,2·media), altrimenti 0,5 | rapporto di partizione 0,5 (§5.1) | **0,5 fisso** con la patch 0001 (D18) |
| intensità del bias δ | `delta` accettato ma **ignorato**: il bias è `ceil(max − min)` dei logit del passo | P_gap = max − min (§4.2) | nessun iperparametro: si segue il codice |
| token anomali (outlier IQR) | calcolati (`detect_outlier`, Q1/Q3, 1,5) ma il loro uso è **commentato** | aggiunti alla green list (§4.3, §5.1) | si segue il codice (sotto) |
| lunghezza L | `TOKEN_LENGTH` = 400: solo il passo dell'estrazione interna, nessun effetto sull'inserimento | L = 400 token | invariato; generazione con il `max_new_tokens` neutro (512) |
| chiave | `hash_key` = 666 | — | `derive_seed(global_seed, "wm-key", "mcgmark", key_id) % 2**31` (SPEC §9.6) |

- **Casi della regola delle discrepanze** (`CLAUDE.md`):
  - γ dinamico: **codice incoerente con sé stesso** (il rilevatore usa γ = 0,5 e non può sapere quale γ è stato usato
    a ogni passo) → si segue il paper con la patch minima 0001, D18, effetto misurato dall'oracle (§9).
  - Bias = scarto dei logit e outlier non usati: **codice coerente con sé stesso ma diverso dal paper** (bias) o con
    una parte disattivata (outlier) → si segue il codice e si documenta. La variante del paper con gli outlier non è
    economica (cambia l'inserimento e quindi tutta la generazione): non si calcola come misura secondaria.
- `default_hparams: {}`: per l'HPO della M8 MCGMark non ha iperparametri da esplorare; l'adapter rifiuta valori
  esterni.

### 3.2 Messaggio (D1, SPEC §9.5)

- Il paper usa 24 bit = 12 di informazione + 12 di correzione (§4.5). Il payload è di **12 bit**.
- **Patch 0000:** `_bits()` costruisce il messaggio di default `format(utente,"04b") + "1111" + format(llm,"04b")`
  (variabili `MCG_USER_ID` = 9, `MCG_LLM_ID` = 6), ma `set_old_water_info(info)` accetta **qualunque stringa**:
  il formato 4+1111+4 è solo il default. **Non serve una nuova patch**: lo shim imposta il messaggio del campione con
  `set_old_water_info(msg12)`. Questo chiude il punto 2 di `cluster_info` §11 per MCGMark.
- Messaggio atteso: 12 bit uniformi da `derive_seed(global_seed, "mcgmark-msg", problem_key, language, model_id,
  sample_index) % 4096`, anche per i negativi (dai loro identificativi).

## 4. Linguaggi

- Paper: "MCGMark focuses solely on Python language" (§4.4). Codice: la macchina a stati riconosce solo costrutti
  Python; in Java, C++ e JavaScript le graffe bloccherebbero interi corpi di funzione.
- **Caso applicato** (regola dei linguaggi): linguaggio dichiarato dal paper e gestito dal codice → **solo Python**;
  gli altri sono `NOT_APPLICABLE` (D17), senza invocare il worker.

## 5. Punteggio di rilevazione e direzione

### 5.1 Punteggio

- Estrazione dal codice estratto (D4), tokenizzato senza token speciali, con il messaggio atteso del campione.
- **Punteggio = bit del primo ciclo completo uguali al messaggio atteso** (0–12). Più alto vuol dire più marcato.
  Decisione nativa: messaggio decodificato uguale all'atteso (come `detection_result`). Tutti i cicli sono in
  `extra["rounds"]` e `extra["round_matches"]`.
- Si usa il primo ciclo perché è l'unico presente nella maggior parte dei campioni e perché i cicli successivi
  dipendono dalla stessa chiave e dalle stesse green list (§7): aggiungerli non è un test indipendente.

### 5.2 Soglia e FPR (da riportare nella tesi, SPEC §13.2)

- Con un codice non marcato i 12 confronti sono circa Bin(12, ½). P(X ≥ 11) = 13/4096 ≈ **0,32%**,
  P(X ≥ 10) = 79/4096 ≈ 1,93%.
- La soglia all'1% di FPR cade quindi su **11 bit**, con un FPR effettivo intorno allo **0,3%**: il punteggio è
  discreto e non si può centrare l'1%.

### 5.3 Bit di correzione (chiuso il 7 ottobre 2026)

- I bit di correzione sono `robust_list[j]`. Nel ramo del bit 1 valgono 1 se il token più probabile era **fuori**
  dalla green list (`is_pure`), e in quel caso il processor lo aggiunge alla green list di generazione ma non a
  quella di rilevazione: si legge 0 e la correzione 1 lo riporta a 1. Nel ramo del bit 0 valgono **sempre 0**
  (`watermark_processor.py`, ramo `else` di `_bias_greenlist_logits`): la lettura del bit 0 non viene invertita.
- L'ipotesi di un errore (bit 0 con correzione 1) era sbagliata: il codice è **coerente con sé stesso** e non serve
  una patch.
- Oracle su codice lungo: nei 4 campioni con un ciclo completo, 48 posizioni d'informazione, **0 errori**
  (25 bit 0 con correzione 0; 12 bit 1 con correzione 0; 11 bit 1 con correzione 1).
- Nota sul registro del repository: in `First_watermark_token` il bit dell'ultima posizione d'informazione di
  un ciclo viene annotato con il bit di correzione (all'annotazione `_cal_watermark_info` è già passato alla
  seconda metà del ciclo; il codice stesso lo segnala con un TODO). È solo un'annotazione: inserimento e
  rilevazione non la usano. La diagnostica dell'oracle confronta quindi con il messaggio atteso, non con quel
  registro.

## 6. Decoding cablato da sovrascrivere

- `watermark.py`: campionamento con `top_k=0` e `sampling_temp`, oppure beam search; `float16` con
  `device_map='auto'`; prompt come testo semplice con token speciali e troncamento; nessun seme per campione.
- Lo shim usa il decoding neutro del request (ADR-006), il prompt chat del framework con il prefill della fence (D20, §2) e il seme del campione.

## 7. Generatore casuale, stato e dispositivo

- **Green list:** `torch.randperm(vocab_size, device=input_ids.device, generator=rng)` con seme
  `hash_key × idc²`, dove `idc` = posizione nel ciclo modulo 12 (0 → 1). Il seme **non dipende dal contesto**: le
  green list sono le stesse per tutti i testi e le posizioni 0 e 1 condividono la stessa. È una proprietà del codice,
  che si segue.
- **Dispositivo:** il generatore è sul dispositivo dei tensori, cioè CUDA in generazione. `randperm` su CUDA e su CPU
  dà permutazioni diverse, quindi anche la **rilevazione gira su GPU** (`gpu_for_detect=True`), senza forward del
  modello.
- **Stato globale di modulo:** `First_watermark_token`, `Bich_kaiwen_First_watermark_token` e `_global_dict`
  (messaggio, `waterinfo_12_global`). Lo shim li azzera prima di ogni campione e di ogni rilevazione; il test di
  isolamento lo verifica.
- **Più sequenze per volta: non supportate** (`scores[0]`, `input_ids[-1]`, stato scalare per sequenza). Ogni item è
  **un campione** con seme proprio `derive_seed(global_seed, "gen", modello, problema, linguaggio, i)`
  (`seed_scheme: per_sample` nel manifest, D9). Il test di equivalenza per riga (`cluster_info` §11, punto 5) non è
  applicabile: il processor vede sempre una sola riga.
- `np.random.seed(hash_key)` a ogni passo: modifica lo stato globale di numpy, non quello di torch; non tocca il
  campionamento.

## 8. Effetti collaterali e dipendenze

- `print` a ogni passo: lo shim li ridirige. Al passo `TOKEN_LENGTH` il processor scrive `Detect.json`,
  `test_output.json` e `test_output.html` in `MCG_RESULT_DIR`, che lo shim fissa a `<run_dir>/mcgmark_results`.
- Decodifica dell'intero vocabolario a ogni passo (`vocab_test`, inutilizzata): rimossa con la patch 0002, senza
  effetti sulla semantica.
- Dipendenze: torch, transformers, scipy, nltk, numpy, colorama (`Watermark/requirements.txt`). Nessuna rete.

## 9. Patch

| Patch | Modifica | Caso |
|---|---|---|
| `0001-gamma-fisso.patch` | `self.gamma = 0.5` al posto della scelta 0,25/0,5 in `__call__` | D18; l'oracle verifica che i logit siano identici all'originale sui passi con γ = 0,5 e misura la quota di passi con γ = 0,25 e i bit recuperati con originale e patch |
| `0002-rimuovi-decodifica-vocabolario.patch` | rimossa `vocab_test = tokenizer.batch_decode(self.vocab)` (variabile mai usata) | solo velocità |
| `0003-rimuovi-calcoli-inutili.patch` | rimossi, a ogni passo, la decodifica dell'intera sequenza, le liste Python dell'intero vocabolario, il criterio di uniformità (sceglieva γ), gli outlier IQR con `np.random` (mai letti); massimo, minimo e argmax con torch sugli stessi valori (`patches/mcgmark/README.md`) | solo prestazioni (decisione dell'utente del 7 ottobre 2026); l'oracle verifica testo identico carattere per carattere a 0000-0002 con lo stesso seme. I calcoli rimossi usavano solo il generatore globale di numpy, che nient'altro legge |

**Tempi (M14).** Le misure di efficienza di MCGMark si riferiscono al codice con la patch 0003. Speed-up della
0003 sull'oracle (codice lungo, 0000-0002 contro 0000-0003, stesso testo carattere per carattere): **3,11×**
(568,8 s → 182,7 s su 9 generazioni, fra 2,51× e 3,29× per campione; A100 80 GB); va
dichiarato nella tesi.

Effetto della patch 0001:
- primo oracle (6 ottobre 2026, senza prefill): il codice originale sceglie γ = 0,25 su **0 dei 748 passi**: i logit non risultano mai "uniformi" (std ≤ 0,2·media, con media dei logit vicina a zero o negativa). Logit di originale e patch identici su tutti i passi. Nessuna posizione marcata, quindi nessun bit da confrontare;
- secondo oracle (6 ottobre 2026, con il prefill): γ = 0,25 su **0 dei 2.054 passi** e su 0 dei 245 passi
  marcati; logit identici su tutti i passi. Su Qwen la patch non cambia l'inserimento.
- Bit recuperati: non ancora confrontabili. Con il prefill i 5 campioni hanno 7-17 posizioni marcate (prima 0),
  ma nessuno arriva al ciclo completo di 24 (tutti `PARTIAL`, 3 su 5 fino a `max_new_tokens` = 512).
- Allineamento della rilevazione: nel secondo oracle la rilevazione sul codice estratto trovava una posizione in
  meno della generazione e nessun token iniziale in comune (spostamento di un passo: in generazione la macchina
  a stati parte dall'ultimo token del prompt). Lo shim ora fa precedere il codice dall'ultimo token del prefill,
  lo stesso che vede il processor al primo passo (`context_ids`); da confermare con il prossimo oracle.

## 10. Definizioni operative degli stati

- **Embed:** `OK` con almeno un ciclo completo (24 posizioni marcate, come `dww` del repository); `PARTIAL`
  altrimenti, anche con 0 posizioni; `FAILED` solo su eccezione.
- **Detect:** `FAILED` se il codice non contiene un ciclo completo di 24 posizioni idonee (estrazione impossibile;
  in M7 vale il punteggio minimo, SPEC §9.5); altrimenti `OK` con punteggio 0–12.
