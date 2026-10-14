# ADR-011 — Dati del Livello 2 (CodeNet) e negativi di sviluppo combinati L1+L2

- **Stato:** accettata (Milestone 9, 13 ottobre 2026).
- **Contesto:**
  - SPEC §10.2, §10.4, §11.3;
  - cluster_info §8.6, §11 (TODO 1, 3, 11, 17);
  - D27, D28;
  - log `M9_codenet_select.txt`, `M9_codenet_preview.txt`, `M9_negatives_compare.txt`.

## Decisione

1. **Selezione.** I 250 problemi e le loro sottomissioni sono in `configs/dataset/codenet_selected.yaml`:
   versionato, generato da `scripts/codenet_select.py`, con i criteri di D28.
   - L'ordine è deterministico: `derive_seed(global_seed, "codenet-select", pid)` per i problemi e
     `"codenet-sub"` per le sottomissioni.
   - Il file riporta il conteggio filtro per filtro, che passa anche nel manifest di `split_L2`.
   - `unverified_accepted_solutions.txt` elenca 272 **problemi** (nessuna sottomissione): sono esclusi.
2. **Testo del problema.** `bench.data.codenet.describe` converte l'HTML in testo:
   - per AtCoder solo la parte inglese;
   - le formule restano in TeX tra `$...$`;
   - formati ed esempi stanno in blocchi recintati con spazi e a capo invariati;
   - si toglie la riga `Score`.

   Il testo entra parola per parola nei template `configs/prompt/user/codenet_<lang>.j2`, approvati il
   13 ottobre 2026 (README della cartella).
3. **Loader `codenet`.** Per ogni problema e linguaggio:
   - prompt = testo convertito, senza `entry_point`;
   - canonica = prima sottomissione scelta;
   - `loc_to_generate` = righe della canonica secondo SPEC §10.3, perché si genera il programma intero;
   - nativi = le altre 5 sottomissioni, con `sample_index` 0-4 per avere `sample_id` distinti.

   Divisione: famiglia `codenet` con 50 dev su 250, stessa parte nei 4 linguaggi (I4).
4. **`prepare_data` con una cella per livello.** La cella L1 è invariata, quindi gli artefatti esistenti si
   saltano. La cella L2 scrive:
   - `split_L2`, `problems/L2_<lang>`, `negatives/L2_<lang>_native`;
   - `negatives/L1L2_<lang>_integration_dev.parquet`.
5. **Integrazione dei negativi di sviluppo (TODO 3, opzione 2).** Con la regola dell'utente (KS dell'opzione 1
   < 0,1 in tutti i linguaggi) il confronto ha dato **opzione 2**:
   - KS dell'opzione 1: 0,17 (Python), 0,63 (Java), 0,63 (C++), 0,46 (JavaScript);
   - KS dell'opzione 2: 0,02-0,07.

   L'integrazione si ricampiona con la procedura di ADR-004 (stessi candidati, filtri, deduplicazione e
   decili), con queste differenze:
   - il riferimento sono i nativi di sviluppo di L1 **e** di L2;
   - le righe sono quelle di SPEC §10.3: `function_loc` non si applica ai programmi completi;
   - il totale di `min_dev_negatives` (2.000) comprende anche i nativi di L2;
   - il seme è `"l1l2_dev_negatives"`.

   Il manifest riporta la KS del pool ottenuto. Il pool di sviluppo per la calibrazione L1+L2 è: nativi L1
   dev + nativi L2 dev + questa integrazione. L'integrazione di L1 resta solo per le soglie provvisorie della
   M7 e della M8.
6. **Executor `codenet`.**
   - Esegue il programma completo con l'input del problema su stdin e confronta stdout, ignorando gli spazi
     finali.
   - Usa la modalità `program` del runner dell'immagine più `io_check.py`, file accessorio del job: non serve
     ricostruire la sandbox.
   - C'è un solo caso di I/O per problema, che coincide con un esempio del testo: è una minaccia alla
     validità per tutti i metodi (D28).
7. **Rilevazione.** Una cella L2 legge solo i nativi di L2 (`neg_human`). L'integrazione combinata si
   rileva come soggetto a sé, `neg_integration`, e solo nelle celle **L2 dev**:
   - è per linguaggio, non per livello; le celle L2 dev sono tutte nuove, mentre aggiungerla alle celle L1
     dev già fatte le farebbe rieseguire per intero (la pipeline rifà una cella se manca anche una sola
     uscita);
   - i punteggi dei nativi di L1 dev non cambiano (stessi codici, stessa configurazione), quindi si
     riusano; quelli dell'integrazione di L1 **non** si usano per le soglie definitive.
8. **Soglie e scelta definitive (L1+L2).** `calibrate` con `levels=[L1,L2]` usa tre parti:
   - nativi di L1 dev: le righe dei nativi di `neg_human` delle celle L1 dev, riconosciute dai
     `sample_id` di `negatives/L1_<lang>_native`;
   - nativi di L2 dev: `neg_human` delle celle L2 dev;
   - integrazione combinata: `neg_integration` delle celle L2 dev.

   La soglia va in `thresholds/<metodo>/<modello>/<lang>/L1L2/<cfg>.json`, con `provisional = false`; il
   manifest riporta `fpr_dev_by_part`, cioè negativi, falsi positivi e FPR per ciascuna parte (FPR di
   sviluppo separato per L1 e per L2, decisione dell'utente del 13 ottobre 2026). Allo stesso modo `tune`
   scrive in `hpo/candidates_L1L2/` e `hpo/selected_L1L2/` e `metrics` in `metrics_L1L2/`. Le soglie e la
   scelta provvisorie della M8 restano nei percorsi di prima e non fanno saltare quelle definitive.
9. **PromptMark su L2 (D27).** L'adapter aggiunge `correctness_loop: false` alla richiesta al worker
   quando i problemi sono di CodeNet; lo shim passa allora `test_list` vuota. Il parametro **non** entra
   nel `config_hash`: la stessa configurazione ha lo stesso hash su L1 e su L2, come serve a `tune`, che
   per ogni candidato legge i due livelli. Il valore è nel manifest dei campioni marcati
   (`native_hparams`), in quello delle metriche (`correctness_loop`) e nel report di `report_hpo.py
   --final`.
10. **Nomi dei job.** Per i livelli diversi da L1 il nome porta il livello (`wmb-detect.sweet_L2`): i job
    di L2 non vanno in conflitto con quelli dell'HPO di L1 dello stesso metodo, i nomi di L1 non cambiano.

## Conseguenze

- Comandi:
  - dati e baseline: `scripts/submit_l2.sh data|canonical|baseline|execute`, stato con
    `scripts/check_l2.py`;
  - metodi su L2 dev e scelta definitiva: `scripts/submit_hpo_l2.sh`, stato con
    `scripts/check_hpo.py <metodo> --level L2`, report con `scripts/report_hpo.py --final`;
  - verifica dell'ADR-009 su L2: `sbatch scripts/m9_promptmark_replay_l2.sh`.
- La nuova integrazione cambia i negativi umani di sviluppo: per le soglie definitive si rileva, per ogni
  configurazione della griglia, solo l'integrazione combinata (soggetto `neg_integration`, percorso
  nuovo); i punteggi dell'integrazione di L1 non si riusano, quelli dei nativi di L1 sì.
- I metodi su L2:
  - SWEET su tutti e 4 i linguaggi;
  - STONE senza JavaScript;
  - ACW, MCGMark e PromptMark solo Python;
  - PromptMark con il ciclo di correttezza spento (D27).
