# Audit di `mcss` (SPEC §9.0; PIPELINE_metodo.md di multimark, §4)

`mcss` è la reimplementazione di Multi-Channel Spread-Spectrum (Choi, Gao, Duan, 2026; arXiv
2607.06009v1; le pagine sono quelle del PDF) come profilo della libreria `multimark` (submodule
`third_party/multimark`).

- **Codice ufficiale.** Non è pubblicato, quindi questo audit descrive gli **algoritmi del
  paper** (punto 2) e come la libreria li realizza.
- **Niente oracle.** Al suo posto c'è l'autovalidazione di PIPELINE §4.4:
  - round trip su 500 frammenti × 100 chiavi (`P1_selftest.txt`);
  - Tabella 3 sui file CodeNet della fascia 32–39 bit;
  - in P2–P4, gli andamenti delle Tabelle 5 e 15.
- **Regola di integrazione (CLAUDE.md).** Non c'è una «discrepanza fra paper e codice», perché il
  codice non c'è. Si implementa dal paper e, dove il paper non specifica, si sceglie una lettura
  dichiarata (§«Deviazioni dal paper»). Un componente che il paper non specifica del tutto non si
  ricostruisce: i suffissi «context-inferred» (MD4, MD19) sono trattati come limite dichiarato.

## 1. Commit e data

- **Libreria:** `third_party/multimark` al commit `488fb5a769241e7887be2ad7f6596e21495ece82`
  (16 novembre 2026). È la pipeline v1.2.7, con `names.single_letter = extend_except_loop` per
  `mcss`; la versione del pacchetto è `0.0.1`.
- **Copia eseguita:** i worker eseguono `build/patched/multimark`, prodotta da
  `scripts/apply_patches.sh multimark` senza patch. `mcss` e `multimark` condividono la copia
  (`patched_dir`).
- **Config hash:** il commit vi entra tramite `MethodAdapter.source_commit`, che legge
  `build/patched/multimark.source_commit`.
- **Paper:** arXiv 2607.06009v1.
- **Specifica:** `PIPELINE_metodo.md` v1.2.8 del repository `multimark`: §3 per la libreria
  comune, §4 per `mcss`.

## 2. Punto d'innesto (algoritmi del paper e loro realizzazione)

**Inserimento (Alg. 1 del paper, p. 5).**

1. Normalizzazione dei nomi delle variabili a `snake_case` e di ogni sito strutturale alla sua
   forma canonica.
2. Pool in ordine di visita in profondità: prima tutte le variabili (2 posizioni ciascuna, uno
   stile su 4 valori), poi i siti strutturali degli 8 canali della Tabella 2.
3. Codifica del messaggio di 24 bit con il codice compatto (`B = 24 + 4t`).
4. Assegnazione delle posizioni del pool ai bit a cicli completi: per il ciclo `k = i // B` una
   permutazione di `0..B−1` (Fisher-Yates guidato dalla chiave), `phi(i) = perm_k[i mod B]`.
5. Scrittura, in ogni sito, del valore del suo bit.

Nella libreria:

- **funzione d'ingresso:** `multimark.embed.embed(code, message, key, cfg, proposer)` con
  `cfg = multimark.config.mcss_profile()`;
- **pool:** `multimark.layout.build_layout`;
- **legame φ:** `multimark.linking.phi_positional` e `permutations` (Fisher-Yates sul flusso
  HMAC, MD2);
- **codice compatto:** `multimark.codec.encode`, matrice `random` (MD7);
- **scrittura:** `multimark.rewrite`, con LibCST (MD1).

Dopo la scrittura il codice si rilegge con `ast`. Un sito che non rilegge il valore richiesto si
annulla (`skipped_sites`), e si riprova fino a 4 volte.

**Rilevazione (Alg. 2, p. 6).**

- **Paper:** rilegge il pool con la stessa costruzione e vota a maggioranza per bit (pareggio o
  nessun voto = cancellazione). Poi decodifica per sole cancellazioni (Teorema 5.2, p. 7) e
  restituisce il messaggio o `FAIL`.
- **Libreria:** `multimark.detect.detect(code, expected, key, cfg, registry_seed=…,
  method="mcss")`. Oltre alla decodifica aperta calcola il punteggio di confronto con il registro
  (§4.2 punto 5, MD5), che serve solo a calibrare la soglia come per tutti i metodi del benchmark.

**Nel benchmark.**

- **Adapter:** `src/bench/methods/adapters/mcss.py`. `McssAdapter` estende
  `PosthocMultibitAdapter` (in `adapters/multimark.py`), che è `CodeEmbedder` + `Detector`.
- **Shim:** `shims/bench_shims/mcss/__main__.py`. `McssShim` estende `PosthocShim` (in
  `shims/bench_shims/multimark/shim_base.py`).
- **Ambiente:** `posthoc`, con `PYTHONPATH` = `shims/` + `build/patched/multimark`.
- **Item di inserimento:** uno per campione della baseline, con il codice estratto e il messaggio
  atteso.
- **Item di rilevazione:** uno per codice, con il messaggio atteso. Per i positivi viene dal
  campione, per i negativi dagli identificativi.

## 3. Iperparametri (protocollo → nativo, §3.11)

I nomi coincidono 1:1 con i campi di `multimark.config.Config`. Il profilo è in
`configs/method/mcss.yaml` ed è uguale a `McssAdapter.PROFILE` (lo verifica
`tests/unit/test_posthoc_adapters.py`). Una configurazione indica solo le chiavi che cambiano.

| Protocollo | Nativo | `mcss` | Paper |
|---|---|---|---|
| `t` | `t` | 2 | `t` = 2 nella fascia D32, 8 da D56 (p. 8); griglia {2} (decisione E di §7) |
| `message_bits` | `message_bits` | 24 (fisso, adapter) | 24 bit = 4 caratteri a 6 bit (p. 4–5) |
| `linking` | `linking` | `positional` | cicli completi con permutazioni Fisher-Yates (p. 5) |
| `mask` | `mask` | `off` | nessuna maschera |
| `canonical` | `canonical` | `paper` | «sorted by type», «ordered by complexity» (p. 5); pareggi MD3 |
| `names.variant` | `names_variant` | `V1` | 4 stili, 2 bit per variabile (p. 5) |
| `names.proposer` | `names_proposer` | `static` (non usato da V1) | — |
| `names.include_params` | `names_include_params` | `true` | non detto; la figura 1 rinomina un parametro (MD8) |
| `channels` | `channels` | `mcss8` | gli 8 canali della Tabella 2 |
| `names.module_level` | `names_module_level` | `true` | «each multi-word variable» (MD14) |
| `code.matrix` | `code_matrix` | `random` | «random by construction» (p. 3), MD18 |
| `names.single_letter` | `names_single_letter` | `extend_except_loop` | testo contraddittorio (MD19) |
| `voting` | `voting` | `simple` | maggioranza semplice (Alg. 2) |
| `channel_weights` | `channel_weights` | `null` | — |
| — | `registry_seed` | `global_seed` | esche del registro (§3.10.1); **non** entra nel `config_hash` |

- I valori ammessi sono copiati dalla libreria nell'adapter, perché bench-core non importa la
  libreria, e i test li confrontano con quelli della libreria.
- `names.proposer=neural` è rifiutato fino a P3.

## 4. Linguaggi

Solo **Python**:

- il paper valuta solo Python (CodeNet Python, p. 7);
- la libreria legge il codice con `ast` e lo scrive con LibCST.

Java, C++ e JavaScript sono `NOT_APPLICABLE` senza invocare il worker
(`supported_languages: [python]`), come per ACW (D6).

## 5. Punteggio di rilevazione e direzione

- **Principale** (§4.2 punto 5, MD5): `score = -log10 P[Bin(N, 1/2) ≥ A]`, calcolato in modo
  esatto con `math.comb`. **Più alto = più marcato.**
  - `N` = voti letti.
  - `A` = voti concordi con la parola di codice del messaggio atteso (`N_reg = 1`, nessuna
    maschera).
- **Secondari** (`secondary_scores`), ognuno con la sua soglia:
  - `nreg16` e `nreg1024`: il massimo sul registro con 15 e 1023 esche,
    `derive_seed(global_seed, "mcss-registry", j)`;
  - `decode`: `bits_correct / 24`.
- **`native_decision`** (diagnostica): decodifica riuscita e uguale al messaggio atteso.
- **`decoded_message`:** è la decodifica aperta (`decoded_message_open`).
- **`bits_correct`:** i bit giusti della decodifica; con decodifica ambigua o incoerente, i bit
  decisi dal voto (decisione L di §7). Lo restituisce `Detector.bits_correct` (ADR-017 §1).
- **`message_accuracy`:** frazione dei positivi con tutti i 24 bit giusti, come nel paper.
- **Soglia:** calibrata all'1% sui negativi umani di sviluppo (I3).
- **Rilevato:** `status = OK`, inserimento non `FAILED` e `score ≥ τ` (§3.10.3).

## 6. Decoding cablato da sovrascrivere

Nessuno, perché il metodo è post-hoc e non genera. La richiesta al worker non ha parametri di
decoding; il codice di partenza è quello estratto dalla baseline del benchmark (D4).

## 7. Dipendenze esterne

- **Pacchetti:** solo `libcst`, nell'ambiente `posthoc` (`environment-posthoc.yml`).
- **Nient'altro:** nessuna rete, nessun token, nessun binario esterno e nessun modello (V1 non
  usa candidati).
- **Hardware:** solo CPU (`gpu_for_embed = gpu_for_detect = False`; regole in
  `configs/resources/default.yaml`).

## 8. Patch

Nessuna (`patches/multimark/README.md`). Le deviazioni dal paper sono scelte della
reimplementazione: sono scritte nella libreria e dichiarate qui sotto, non sono patch.

## 9. Definizioni operative degli stati

**Inserimento (§3.9).** N = bit potenziali (2 per variabile e 1 per sito strutturale); con
`t = 2`, B = 32.

- **`OK`:** `N ≥ B`.
- **`PARTIAL`:** `0 < N < B`. È un positivo a tutti gli effetti (I2 esclude solo `FAILED`), e il
  codice marcato è quello dello shim.
- **`FAILED`:** il motivo va in `error` e in `extra["reason"]`.
  - `no_sites`: N = 0.
  - `syntax_error`: errore di sintassi.
  - `rewrite_mismatch`: la rilettura non torna dopo 4 tentativi.
  - Un'eccezione dello shim dà `FAILED` con il traceback (runner).

  Con `FAILED` il codice in uscita è quello della baseline, che conta per Pass@1.

**Rilevazione.**

- **`OK`:** c'è almeno un voto.
- **`FAILED`:** `no_sites` (nessun voto) o `syntax_error`; il punteggio vale allora il minimo.

## Deviazioni dal paper

La fonte è `third_party/multimark/docs/mcss_deviations.md`, con gli stessi ID. La tabella le
riassume con il caso della regola di integrazione applicato; per le decisioni con numeri (MD19)
segue il dettaglio.

| ID | Paper | Deviazione (sintesi) | Caso |
|---|---|---|---|
| MD1 | p. 8, `ast.unparse` | Lettura sull'`ast`, scrittura con LibCST: commenti e formattazione restano, pool identico | scelta di realizzazione, dichiarata |
| MD2 | p. 5, punti su secp256k1 | Flusso di Fisher-Yates da HMAC-SHA256, `prf_int(K, "fy", j)`, consumato in ordine (decisione I) | scelta di realizzazione; da riverificare se esce il codice |
| MD3 | p. 5, «sorted by type» | Pareggi di `kind` nei canali 2 e 3: ordine lessicografico del testo dell'operando | interpretazione dichiarata |
| MD4 | p. 5, «context-inferred suffixes» | Suffisso dei nomi di una parola dal tipo del primo binding (`_count`, `_str`, …, altrimenti `_val`) | componente non specificato: regola nostra semplice |
| MD5 | — | Punteggio continuo per la calibrazione (concordanza a `N_reg = 1`); `native_decision` = decodifica giusta | adattamento al protocollo (D2) |
| MD6 | p. 8, «100% AST validity» | Condizioni semantiche sui canali più strette (siti persi misurati con `--no-guards`) | correzione di correttezza, dichiarata |
| MD7 | p. 3, seme non pubblicato | Matrice del codice compatto nostra, con la stessa costruzione | dato non disponibile |
| MD8 | p. 2, figura 1 | Parametri inclusi nel canale dei nomi | interpretazione dichiarata |
| MD9 | p. 5, canali 2 e 3 | Operandi con stesso `kind` e stesso testo: il nodo non è un sito | conseguenza della definizione |
| MD10 | — | Regole di non interferenza fra canali (un nodo, un sito; esclusioni mirate) | necessaria perché la lettura torni |
| MD11 | p. 5, canale dei nomi | Variabili ammesse come in §3.6.1, allineate a T1.4 | regola non data dal paper |
| MD12 | p. 5, `list_comp` | Condizioni precise su `L` e `x` (nessun riferimento a `x` fuori dal sito) | correttezza semantica |
| MD13 | — | Funzioni con lo stesso `fid` condividono le chiavi (riguarda `multimark`, non `mcss`) | — |
| MD14 | p. 5; Tabella 3 | Variabili di modulo nel canale dei nomi | interpretazione, motivata dalla Tabella 3 su CodeNet |
| MD15 | — | Nessun sito dentro le f-string | affidabilità delle posizioni `ast` (Python < 3.12) |
| MD16 | p. 5, rinomina di stile | Regola di scope per i nomi nuovi (v1.2.1) | regola non data dal paper |
| MD17 | — | `list()` e `dict()` senza argomenti contano come puri | non interferenza con `empty_coll` |
| MD18 | p. 3, p. 7 | `code.matrix = random` per `mcss` (fedeltà); `dmin4` solo per `multimark` | fedeltà |
| MD19 | p. 5, passo 2 | Variabili di una lettera: `extend_except_loop`, scelta con i dati (dettaglio sotto) | testo contraddittorio: scelta con una regola fissata prima |

**Effetti misurati finora** sui 169 file della selezione finale:

- media `n_bits_v1` 34,61 contro 34,6: il criterio della Tabella 3 è superato;
- variabili +8,3%, siti strutturali −19,1%.

Lo scarto per componente si dichiara (MD19); il criterio di accettazione è sul totale (§4.4
punto 2).

Nota sul canale dei nomi e T1.4: T1.4 (`attacks/t1/rename.py`) rinomina solo nomi negli scope
di funzione (`FunctionScope`). Le variabili di modulo di `mcss` (MD14) non sono quindi colpite da
T1.4, ma lo saranno da T1.6 (§5.5). Lo si è verificato in P2, come chiesto da PIPELINE §3.6.1.

### MD19 — Variabili di una lettera (`names.single_letter`; PIPELINE v1.2.7 §4.2 punto 9)

**Il problema.** Al passo 2 di p. 5 il paper estende i nomi di una parola con un suffisso e porta
come esempi `n → n_count` e `s → s_str`, cioè proprio nomi di una lettera. Nella stessa frase però
dice che le variabili di un carattere «come `i`» non possono portare uno stile. Il testo si
contraddice e il codice ufficiale non è disponibile. Il caso rientra fra le discrepanze in cui un
componente non è specificato del tutto: la lettura si sceglie con i dati e lo scarto si dichiara.

**Le tre letture** (opzione `names.single_letter` della libreria):

- `exclude`: regola (c) di §3.6.1, `len(n) ≥ 2`. Era la lettura usata fino a v1.2.6.
- `extend_except_loop`: i nomi di una lettera diventano siti e si estendono con il suffisso di
  §4.2 punto 4. Fanno eccezione i nomi che nell'unità sono bersaglio di un `for` o di una
  comprehension, anche una sola volta.
- `extend_all`: tutti i nomi di una lettera diventano siti.

**La regola di scelta** (§4.2 punto 9), fissata prima di vedere i dati:

- vince la lettura il cui rapporto fra siti strutturali e variabili, sui file della fascia
  32–39 bit, è più vicino a 0,92 (Tabella 3 di MCSS: 10,9 / 11,9);
- se due letture distano da 0,92 meno di 0,05 l'una dall'altra, vince quella con la media di
  variabili più vicina a 11,9;
- si usano solo file mai di test.

**Dati.**

- **Log:** `docs/milestone_logs/P1_single_letter.txt`, libreria `fb31176`.
- **Pool:** allargato, `L2_codenet_pool_extra`.
- **File:** 27.040 ammessi, con gli stessi filtri della selezione: niente `split == "test"`,
  niente problemi nello stesso cluster di un problema di test, niente errori di parsing.
- **Profilo:** `mcss_mod`, cioè forme canoniche del paper, parametri e variabili di modulo
  inclusi, canali 1–8.

La fascia si ricalcola per ogni lettura, perché `n_bits_v1` = strutturali + 2 · variabili cambia
con la lettura. Il rapporto è il rapporto delle medie.

| sui file della fascia 32–39 bit | `exclude` | **`extend_except_loop`** | `extend_all` | MCSS T3 |
|---|---:|---:|---:|---:|
| file in fascia | 237 | **693** | 982 | — |
| problemi in fascia | 101 | **169** | 195 | — |
| media `n_bits_v1` | 34,80 | **34,93** | 35,13 | 34,6 |
| media variabili | 10,32 | **12,88** | 13,37 | 11,9 |
| di cui variabili di modulo | 5,16 | **7,22** | 8,25 | — |
| media siti strutturali | 14,15 | **9,17** | 8,40 | 10,9 |
| rapporto strutturali / variabili | 1,37 | **0,71** | 0,63 | 0,92 |
| distanza dal rapporto 0,92 | 0,45 | **0,21** | 0,29 | — |
| distanza dalla media di variabili 11,9 | 1,58 | **0,98** | 1,47 | — |

Sugli stessi 27.040 file ammessi, senza limitarsi alla fascia:

- il rapporto vale 1,58, 0,64 e 0,54;
- la media di variabili per file vale 2,01, 4,99 e 5,86.

**Esito.** La lettura scelta per `mcss` è **`extend_except_loop`**.

- Ha il rapporto più vicino a 0,92 e nessun'altra lettura le sta alla pari entro 0,05: la
  seconda, `extend_all`, è a 0,29, cioè 0,08 più lontana.
- Sui suoi file in fascia il criterio della Tabella 3 è superato: la media di `n_bits_v1` è 34,93
  contro 34,6 (+1,0%, entro ±10%).
- La libreria `488fb5a` (pipeline v1.2.7) mette `names_single_letter = "extend_except_loop"` nel
  profilo `mcss_profile()`.
- La base di `multimark` resta su `exclude`: per quella vale la decisione R di §7, in P5.

**Scarto residuo dal paper**, sui 693 file in fascia con `extend_except_loop`:

| quantità | nostra | paper | scarto |
|---|---:|---:|---:|
| bit | 34,93 | 34,6 | +1,0% |
| variabili | 12,88 | 11,9 | +8,2% |
| siti strutturali | 9,17 | 10,9 | −15,9% |
| rapporto strutturali/variabili | 0,71 | 0,92 | −0,21 (−23%) |

Il paper sta fra `exclude` (rapporto 1,37) ed `extend_except_loop` (0,71). La spiegazione più
probabile è che estenda solo i nomi di una lettera di cui riesce a dedurre il tipo
(«context-inferred suffixes»). Il testo non specifica questa regola, quindi non la si ricostruisce
(regola di integrazione per i componenti non specificati del tutto) e lo scarto si dichiara come
limite della reimplementazione.

**Controllo.** Con la libreria `fb31176` la rilettura con `exclude` dà la stessa tabella ottenuta
con `1712e38` (stesso sha256, `2fa36ef5…`): le modifiche alla libreria fra i due commit non
cambiano il conteggio.

**Selezione per l'autovalidazione** (pipeline v1.2.8).

- **Come:** rifatta sulla tabella `extend_except_loop` già calcolata, con la stessa fascia 32–39,
  gli stessi filtri e una sottomissione per problema (`docs/milestone_logs/P1_mcss_selftest_eel.txt`,
  selezione `mcss_selftest_eel_fb311767f65c.jsonl`).
- **File:** 169 da 169 problemi: 13 `extra_dev`, 123 `extra_unused`, 33 `unselected`; nessun file
  di test né di negativi.
- **Medie sui file scelti:** bit 34,61 contro 34,6 (+0,0%), variabili 12,89 (+8,3%), siti
  strutturali 8,82 (−19,1%).
- **Uso:** è la selezione del criterio (a) di P2 e della riproduzione della Tabella 5.

La selezione precedente con `exclude` (101 file, `P1_mcss_selftest_extra.txt`) resta com'è.
