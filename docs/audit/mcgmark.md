# Audit di MCGMark (SPEC §9.0)

- **Stato:** decisioni dell'utente del 6 ottobre 2026 (messaggio di 12 bit, γ fisso, seme per campione, solo
  Python) applicate; resta aperto il punto del §5.3, da chiudere con i numeri dell'oracle.
- **Repository:** `KevinHeiwa/MCGMT`, paper "MCGMark: An Encodable and Robust Online Watermark for Tracing
  LLM-Generated Malicious Code" (arXiv 2408.01354v2).

## 1. Commit e data

- Commit `eefa27b6` (submodule `third_party/MCGMT`).
- Il codice eseguito è la copia `build/patched/mcgmark/` (ADR-002) con tre patch:
  - `0000` (preesistente, dell'utente): import riparati, moduli `homoglyphs`/`normalizers` copiati da
    lm-watermarking, simboli mancanti di `watermark_global.py` ricostruiti (§3.2);
  - `0001` (nuova): γ = 0,5 fisso (D18);
  - `0002` (nuova): rimossa la decodifica inutile dell'intero vocabolario a ogni passo.
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

### 5.3 Punto aperto: bit di correzione (da chiudere con l'oracle)

- I bit di correzione del repository sono `robust_list[j]` = 1 se, alla posizione d'informazione j, il token più
  probabile era **fuori** dalla green list (`is_pure`), indipendentemente dal bit inserito.
- Ragionando sul codice:
  - bit 1, token più probabile fuori: il processor lo aggiunge alla green list di generazione ma non a quella di
    rilevazione, quindi si legge 0; correzione 1 → 0 XOR 1 = 1, corretto;
  - bit 0, token più probabile fuori: si legge 0, ma la correzione vale comunque 1 → 0 XOR 1 = 1, **sbagliato**.
- Se il ragionamento è giusto, circa un quarto dei bit d'informazione verrebbe invertito (bit 0 × token più probabile
  fuori dalla green list, circa ½ × ½), e il messaggio completo si recupererebbe raramente.
- L'oracle lo misura (`test_known_message_is_recovered` stampa gli errori per bit del messaggio e bit di
  correzione). Se il problema si conferma è un caso di **codice incoerente con sé stesso** e la regola indica una patch
  minima secondo il paper (correzione = 1 solo quando la lettura del bit sarebbe sbagliata); prima di scriverla
  mostrerò i numeri.

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

Effetto della patch 0001:
- primo oracle (6 ottobre 2026, senza prefill): il codice originale sceglie γ = 0,25 su **0 dei 748 passi**: i logit non risultano mai "uniformi" (std ≤ 0,2·media, con media dei logit vicina a zero o negativa). Logit di originale e patch identici su tutti i passi. Nessuna posizione marcata, quindi nessun bit da confrontare;
- con il prefill: quota di passi con γ = 0,25 e bit recuperati con originale e patch _in attesa_ del nuovo oracle.

## 10. Definizioni operative degli stati

- **Embed:** `OK` con almeno un ciclo completo (24 posizioni marcate, come `dww` del repository); `PARTIAL`
  altrimenti, anche con 0 posizioni; `FAILED` solo su eccezione.
- **Detect:** `FAILED` se il codice non contiene un ciclo completo di 24 posizioni idonee (estrazione impossibile;
  in M7 vale il punteggio minimo, SPEC §9.5); altrimenti `OK` con punteggio 0–12.
