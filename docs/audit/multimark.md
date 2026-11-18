# Audit di `multimark` (SPEC §9.0; PIPELINE_metodo.md di multimark, §3)

`multimark` (MultiMark, nome provvisorio) è il metodo proposto dalla tesi:

- **Tipo:** watermark multi-bit post-hoc per codice Python, 24 bit.
- **Libreria:** `multimark`, la stessa di `mcss` (submodule `third_party/multimark`). Non c'è un
  paper: la specifica vincolante è `PIPELINE_metodo.md` v1.2.8, e questo audit ne descrive la
  realizzazione e la configurazione di base usata dal benchmark.
- **Differenze da MCSS:** sono il contenuto della sezione finale.

## 1. Commit e data

- **Libreria:** `third_party/multimark` al commit `488fb5a769241e7887be2ad7f6596e21495ece82`
  (16 novembre 2026), versione del pacchetto `0.0.1`.
- **Copia eseguita:** `build/patched/multimark`, condivisa con `mcss`, zero patch.
- **Config hash:** il commit vi entra tramite `MethodAdapter.source_commit`.
- **Specifica:** `PIPELINE_metodo.md` v1.2.8. Stato: **P1 chiusa** il 16 novembre; questo audit
  apre P2.
- **ClassEval fuori da P2** (decisione di Ivan del 16 novembre, pipeline v1.2.9). ClassEval è L3,
  solo `split=test`, e per ADR-013 nessuno stage gira sul test prima del congelamento. Il
  criterio (b) si misura quindi solo su L2 dev.
- **Esempi prima/dopo** dei due metodi (siti, voti, punteggio): `docs/reports/P2_esempi_bench.md`
  (`scripts/posthoc_examples.py`).

## 2. Punto d'innesto

**Inserimento:** `multimark.embed.embed(code, message, key, cfg, proposer)` con la configurazione
di base (§0.1, §3.11). I passi sono quelli di §3.9:

1. lettura con `ast` dei siti strutturali dei 14 canali e delle variabili sito;
2. **parola di codice** `cw = m ∥ m·P` (codice compatto, matrice `dmin4`, B = 32);
3. **legame per funzione e canale** (2A): `phi(s) = (off(fid, c) + rank(s)) mod B`, con
   `off = prf_int(K, "offset", name, n_params, c)`;
4. **maschera** (1B): `cw[phi] XOR prf_bit(K, "mask", …)` per i siti strutturali;
5. **canale dei nomi V2**: il nome porta `prf_bit(K, "name", norm(n))`; se il bit non è quello
   richiesto, si prende il primo candidato del proponente con il bit giusto (statico: sinonimi,
   suffissi, prefissi, fino a 32 candidati);
6. **scrittura** con LibCST e rilettura completa con `ast`: i siti incoerenti si annullano.

**Rilevazione:** `multimark.detect.detect(code, expected, key, cfg, registry_seed=…,
method="multimark")`, secondo §3.10:

- voti `(phi, valore XOR maschera)` di tutti i siti letti;
- punteggio di confronto con il registro (1A) a `N_reg ∈ {1, 16, 1024}`;
- decodifica aperta per cancellazioni.

**Nel benchmark:**

- **Adapter:** `src/bench/methods/adapters/multimark.py`. `MultimarkAdapter` estende
  `PosthocMultibitAdapter`, la base comune con `mcss`, che è `CodeEmbedder` + `Detector`.
- **Shim:** `shims/bench_shims/multimark/` (`MultimarkShim` estende `PosthocShim` di
  `shim_base.py`).
- **Ambiente** `posthoc`. Per gli item di inserimento e di rilevazione vale quanto scritto per
  `mcss` (audit di `mcss`, punto 2).

## 3. Iperparametri (protocollo → nativo, §3.11)

Il profilo di base è in `configs/method/multimark.yaml` ed è uguale a `MultimarkAdapter.PROFILE`.
Una configurazione dell'HPO o dell'ablazione indica solo le chiavi che cambiano.

| Protocollo | Nativo | Base | Note |
|---|---|---|---|
| `t` | `t` | 2 | B = 24 + 8 = 32; ablazione t ∈ {0, 4} |
| `message_bits` | `message_bits` | 24 (fisso, adapter) | «24 bit contro 12» di MCGMark (D1, §0.3) |
| `linking` | `linking` | `function_channel` (2A) | ablazione `positional`, `function_single_run` |
| `mask` | `mask` | `on` (1B) | ablazione `off` |
| `canonical` | `canonical` | `kind` (2D) | ablazione `paper` |
| `names.variant` | `names_variant` | `V2` | ablazione V0, V1, V1b, V2b; scelta fuori dall'HPO (§6.4) |
| `names.proposer` | `names_proposer` | **`static`** | `neural` arriva in P3: fino ad allora è rifiutato e `gpu_for_embed = False` |
| `names.include_params` | `names_include_params` | `false` | decisione C (chiusa il 13 novembre) |
| `channels` | `channels` | `extended` (1–14) | ablazione `mcss8`; `extended+str` solo con la decisione P |
| `names.module_level` | `names_module_level` | **`true`** | decisione O, provvisoria (v1.2.4); conferma a fine P2 (ΔPass@1 su L2 dev con entrambi i valori) |
| `code.matrix` | `code_matrix` | `dmin4` | MD18, controllo di §3.8 superato (`P1_codec_matrices.txt`) |
| `names.single_letter` | `names_single_letter` | `exclude` | decisione R in P5 |
| `voting` | `voting` | `simple` | pesato in P5, con i pesi dalla flip rate |
| `channel_weights` | `channel_weights` | `null` | solo con `voting` pesato |
| — | `registry_seed` | `global_seed` | esche (§3.10.1); **non** entra nel `config_hash` |

Note:

- **Modulo, adapter contro libreria.** `names.module_level` vale `true` nell'adapter ma `false`
  nel default della libreria (`multimark_base()`), che resta quello di prima della decisione O.
  `tests/unit/test_posthoc_adapters.py` verifica che sia l'unica differenza fra i due profili.
- **Griglie.** `configs/hpo/multimark.yaml` ha la base (§6.4) e, dalla pipeline v1.3.0, due
  punti di §6.3 anticipati su sviluppo (`names.single_letter=extend_except_loop`,
  `names.include_params=true`), ognuno con il suo `config_hash`; si lanciano come sweep
  (`hpo.grid=multimark`) nelle fasi watermark, execute, detect, calibrate e metrics (ADR-017
  §5) del secondo giro di P2, e la fase `tune` non si lancia finché la scelta non si fa con il criterio di §6.4 (P5).
  `configs/hpo/multimark_ablation.yaml` ha i punti di §6.3 che si possono già definire.

## 4. Linguaggi

Solo **Python**, per la stessa ragione di `mcss` (lettura con `ast`, scrittura con LibCST; §0.2).
Le altre celle sono `NOT_APPLICABLE` senza worker.

## 5. Punteggio di rilevazione e direzione

- **Principale:** `-log10 P[Bin(N, 1/2) ≥ A]` con `N_reg = 1`, cioè la concordanza dei voti con
  la parola di codice del messaggio atteso. **Più alto = più marcato.**
- **Secondari:** `nreg16`, `nreg1024` (registro di esche `multimark-registry`) e `decode`
  (`bits_correct / 24`), ognuno con la sua soglia.
- **Attribuzione:** i messaggi scelti dal registro (`best_nreg16`, `best_nreg1024`) danno
  `wrong_attribution_rate_<nome>` (ADR-017 §3).
- **`native_decision`** (diagnostica): `P · N_reg ≤ 0,01` con `N_reg = 1`.
- **Capacità:**
  - `decoded_message` = decodifica aperta;
  - `bits_correct` da `extra` (ADR-017 §1);
  - `message_accuracy` su tutti i positivi e `message_accuracy_detected` sui rilevati (ADR-017
    §2).
- **Soglia:** all'1% sui negativi umani di sviluppo.
- **Siti minimi analitici:** la tabella di §3.10.3 (7 siti a f = 0 con `N_reg = 1`).

## 6. Decoding cablato da sovrascrivere

Nessuno: il metodo è post-hoc, senza generazione. In P3 il proponente neurale userà un modello
piccolo con decoding greedy (`max_new_tokens = 64`, §3.6.4), che sarà da registrare qui.

## 7. Dipendenze esterne

- **Pacchetti:** `libcst`.
- **Nient'altro:** nessuna rete, nessun token e nessun binario esterno.
- **Hardware:** solo CPU fino a P2 (proponente statico, dizionario
  `multimark/data/name_synonyms.json` nel pacchetto).
- **Da P3:** `torch`/`transformers` e il modello dei nomi dalla cache del cluster (decisione J);
  allora `gpu_for_embed` passerà a `True` per `names.proposer=neural`.

## 8. Patch

Nessuna (`patches/multimark/README.md`). Il metodo si modifica solo nel repository `multimark`,
poi si aggiorna il submodule (§0.4).

## 9. Definizioni operative degli stati

Come per `mcss` (§3.9). N = bit potenziali: 1 per sito strutturale e 1 per variabile con V2; B =
32.

**Inserimento.**

- **`OK`:** `N ≥ B`.
- **`PARTIAL`:** `0 < N < B`. È il caso del codice corto che il metodo vuole coprire: il campione
  è un positivo e il codice marcato è quello dello shim.
- **`FAILED`:** motivo in `error` ed `extra["reason"]`; il codice resta quello della baseline.
  - `no_sites`: N = 0. Su L1 dev è il 22–45% dei campioni (P0), atteso e non un errore: criterio
    (c) di P2.
  - `syntax_error`: errore di sintassi.
  - `rewrite_mismatch`: la rilettura non torna.
  - Eccezione dello shim.

Contatori in `extra`:

- `skipped_sites`: siti annullati;
- `n_renamed`: variabili rinominate;
- `n_names_no_candidate`: variabili senza un candidato con il bit giusto (voto sbagliato
  dichiarato).

**Rilevazione.** `OK` se c'è almeno un voto; `FAILED` con `no_sites` o `syntax_error`.

## Differenze rispetto a MCSS

Le scelte del metodo che lo distinguono dalla reimplementazione (§0.1, §3) sono queste:

- **identificazione con il registro** (1A);
- **maschera con la chiave** (1B);
- **legame per (funzione, canale)** con offset dal `fid` (2A), così estrarre o riordinare
  funzioni non sposta i bit delle altre;
- **forme canoniche indipendenti dai nomi** (2D);
- **14 canali**: gli 8 di MCSS più 6 nuovi, §3.3.1b;
- **nomi con chiave V2** invece degli stili;
- **parametri esclusi**;
- **matrice `dmin4`**.

Ogni scelta ha un punto di ablazione (§6.3). Le regole comuni di lettura e scrittura (MD1, MD6,
MD9–MD17 dell'audit di `mcss`) valgono anche qui. T1.4 rinomina solo nomi negli scope di funzione:
le variabili di modulo (decisione O) non sono colpite da T1.4 ma lo saranno da T1.6 (§5.5).

## Secondo giro di P2 (pipeline v1.3.0, 17 novembre 2026)

La libreria `4ea0d43` (submodule a `475d4cc`) corregge i casi del criterio (a) del primo giro
(MD20–MD26 di `docs/mcss_deviations.md` della libreria). Il giro si rifà senza toccare il primo:

- **Celle distinte.** Il `config_hash` contiene il commit della copia patchata
  (`build/patched/multimark.source_commit`, ADR-003): con la libreria nuova `mcss` passa da
  `d68986489777` a `1d0c64c5124e`, `multimark` da `194b0aa80f9f` a `59a04735824c`, quindi tutti i
  percorsi di `watermarked/`, `execution/`, `detection/`, `thresholds/` e `metrics_L1L2/` sono
  nuovi. La pipeline salta solo le celle con tutte le uscite già presenti e nessuna fase scrive su
  un'uscita esistente. Il controllo che conta è che submodule, copia patchata e commit atteso
  coincidano (con la copia vecchia gli hash tornerebbero quelli del primo giro e la pipeline
  salterebbe tutto): `scripts/run_p2_round2.sh` lo fa prima di ogni fase.
- **Inventario e selezione.** `scripts/inventory_sites.sh selftest_r2`: tabella `mcss` con
  `--linking positional --name-variant V1` (le condizioni del lettore di MD20 e MD23 dipendono dal
  legame e dalla variante dei nomi), selezione e dettaglio della fascia in uscite separate per
  commit (`mcss_selftest_r2_<c>.*`).
- **Uscite non separate per hash.** Le tabelle di `scripts/report_capacity.py` avevano un nome
  senza l'hash e venivano riscritte: ora il default è `capacity/<config_hash>/` e una tabella
  diversa da quella esistente ferma lo script. `scripts/report_metrics.py --config` stampa solo
  le celle del secondo giro.
- **Soglia teorica fissa.** `report_metrics.py` riporta per `mcss` e `multimark`, accanto alla
  soglia calibrata del benchmark (che resta la regola), TPR, FPR umano e FPR LLM con
  `score >= 2` (p ≤ 0,01), per livello. È una riga in più del riepilogo, non una metrica della
  fase `metrics`.

## Giro di capacità su sviluppo (pipeline v1.3.3, 18 novembre 2026)

Libreria `a4e6972` (pipeline v1.3.3; opzioni `names.bits_per_name` e `canonical.same_kind`, MD27
e MD28; `names.module_level=true` anche nel default della libreria). Il giro si rifà senza
toccare i precedenti (`scripts/run_p2_round3.sh`, log `P2_round3.txt`):

- **Iperparametri nuovi.** L'adapter passa anche `names.bits_per_name` (1, 2) e
  `canonical.same_kind` (`off`, `binding_order`), 1:1 con `Config.names_bits_per_name` e
  `Config.canonical_same_kind`; tutti e due i profili hanno 1 e `off` (come la libreria).
  L'adapter rifiuta, come la libreria, `bits_per_name=2` senza `names.variant=V2` e
  `binding_order` senza `canonical=kind`. Le due chiavi entrano negli iperparametri nativi, quindi
  nel `config_hash`: gli hash dei giri precedenti scritti nei log non si ricalcolano più con il
  codice attuale (per rilanciare quei giri serve wm-bench `48df7da`).
- **Configurazioni.** `mcss` b521002e09f5; `multimark` base 3c1bd11758fc; punti di
  `configs/hpo/multimark.yaml`: `single_letter=extend_except_loop` 4937d9ddd78a, A
  (`canonical.same_kind=binding_order`) 770f999adefb, B (`names.bits_per_name=2`) cb79b2fcb4e6, C
  (single_letter + binding_order + bits_per_name=2) cf7e4c9f106e, D (C + `include_params=true`)
  37400fdcd589. Il punto `include_params=true` da solo non si rimisura.
- **Attacchi.** Insieme S di v1.3.3 (T1.4 natural r = 1,0; T1.4 random r = 0,5; T1.5 k = 5; T2.1
  b = all; T2.4 p = 0,5; T4.1 rename = true): si lanciano le griglie intere di T1.4, T1.5, T2.1,
  T2.4 e T4.1 (19 punti) perché la pipeline non sceglie singoli punti; il report usa solo S.
- **Report** (`scripts/report_round3.py`): TPR alla soglia calibrata (nuova, L1+L2) e alla
  soglia teorica per condizione di S e fascia di siti; il criterio di v1.3.3 (media su S del TPR
  calibrato, ogni condizione su tutti i campioni di L1+L2 e dei due modelli) con i vincoli
  ΔPass@1 ≤ 3 pp per cella e FPR umano alla soglia teorica ≤ 1% per livello (impercettibilità in
  P5); la curva in K di §5.11 per `mcss`, base e C, con il controllo sulle esche `nreg16` e
  `nreg1024`.
