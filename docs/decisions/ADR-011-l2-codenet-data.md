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
7. **Rilevazione.** Una cella L2 legge solo i nativi di L2. L'integrazione combinata si aggiunge alle celle
   di sviluppo di L1 quando la calibrazione usa L1+L2 (passo successivo della M9).

## Conseguenze

- Comandi:
  - `scripts/submit_l2.sh data|canonical|baseline|execute`;
  - stato con `scripts/check_l2.py`.
- La nuova integrazione cambia i negativi umani di sviluppo di L1: per le soglie definitive la loro
  rilevazione va rifatta per tutte le configurazioni della M7 e della M8, su percorsi nuovi, così non si
  riusano i punteggi vecchi.
- I metodi su L2:
  - SWEET su tutti e 4 i linguaggi;
  - STONE senza JavaScript;
  - ACW, MCGMark e PromptMark solo Python;
  - PromptMark con il ciclo di correttezza spento (D27).
