# ADR-006 — Generazione della baseline: prompt, decoding neutro, estrazione, SLURM

- **Stato:** accettata (Milestone 3, 3 ottobre 2026; prompt e decoding decisi dall'utente)
- **Contesto:** SPEC §2.1, §7.5, §10.5, §10.6, §16.3; cluster_info §1 (limiti di tempo, ripresa obbligatoria), §4, §6, §7.

## Decisioni

1. **System prompt** (decisione dell'utente), in `configs/prompt/system.txt`, **bloccato**:
   `configs/prompt/default.yaml` contiene il suo SHA-256
   (`1598d00e2c894a83cfa32a93c37623b59811a6144afbcfd98e20c9ddab122c6e`) e un file diverso dà `ConfigError`.
   L'a capo finale del file non fa parte del prompt. L'hash va nel manifest di ogni baseline.

2. **Template utente** `configs/prompt/user/<dataset>_<linguaggio>.j2` (testi approvati dall'utente):
   - stessa struttura per HumanEval+, MBPP+ e HumanEvalPack Java, C++ e JS;
   - linguaggio esplicito, richiesta di mantenere nomi e firme (i test chiamano `entry_point` per nome);
   - prompt del dataset parola per parola dentro un blocco recintato del linguaggio.

   Gli SHA-256 dei template vanno nel manifest. I messaggi sono `[system, user]` e si applica il chat template
   nativo del modello (`apply_chat_template(..., add_generation_prompt=True)`).

3. **Decoding neutro** (decisione dell'utente).
   - Valori di SPEC §2.1: temperature 0.2, top_p 0.95, max_new_tokens 512 (L1) o 1024 (L2–L4), N = 6.
   - Tutto il resto è disattivato: `do_sample=True`, `top_k=0`, `repetition_penalty=1.0`,
     `no_repeat_ngram_size=0`, `num_beams=1`, nessun altro filtro o vincolo.
   - Il `GenerationConfig` è costruito da zero e sostituisce `model.generation_config`, così i
     `generation_config.json` dei modelli (per esempio top_k=20 e repetition_penalty>1 di Qwen) non hanno effetto.
     Dal modello si prendono solo eos e pad.
   - La configurazione effettiva va nel manifest.
   - **Gli shim dei metodi (M5, M6) devono applicare la stessa configurazione neutra**
     (`bench.generation.decoding.neutral_settings`): annotato in cluster_info §11.

4. **Seme** `derive_seed(global_seed, "gen", model_id, problem_key, language)` (SPEC §10.5): si fissa con
   `torch.manual_seed` e `torch.cuda.manual_seed_all` prima di un `generate` per problema con
   `num_return_sequences = 6`. Un problema alla volta, così il seme vale per problema indipendentemente
   dal raggruppamento. Lo stesso seme sulla stessa GPU dà lo stesso output (verificato dal test `gpu`); tra
   GPU diverse il determinismo bit a bit non è garantito (SPEC §18, D9).

5. **Estrazione del codice** (SPEC §10.6, D4), identica per baseline e metodi:
   1. primo blocco chiuso con il tag del linguaggio (alias `py`, `c++`/`cc`, `js`, …); poi il primo blocco chiuso
      senza tag; poi un blocco aperto e mai chiuso (output troncato da `max_new_tokens`); poi il testo intero se
      non è vuoto e si analizza senza errori;
   2. HumanEval+ e HumanEvalPack: se il codice non definisce l'`entry_point` (funzioni tree-sitter, arrow
      function JS, metodi Java), si antepone il prompt del dataset. MBPP+: nessuna anteposizione;
   3. altrimenti `code=""`, `extraction_ok=False`.

   Il tasso di estrazione (complessivo e per dataset) va nel manifest.

6. **Ripresa** (cluster_info §1).
   - Dopo ogni problema i 6 campioni si scrivono in `baseline/<modello>/_partial/<cella>.jsonl`, con `fsync`.
   - Il file inizia con un'impronta di modello, decoding, hash dei prompt e seme globale: se la configurazione
     cambia, il file parziale si scarta.
   - A fine cella si scrive il Parquet con il manifest e il file parziale si cancella.

7. **SLURM** (`bench submit`). Aggiornato il 3 ottobre 2026 dopo il primo tentativo sul cluster.
   - **Un solo job SLURM per fase, che esegue le celle (modello, livello, linguaggio, parte) in sequenza.**
     Il primo invio, un job array di 16 job, è stato rifiutato da `sbatch` (`AssocMaxSubmitJobLimit`): il
     cluster universitario è condiviso e limita i job per utente. Su richiesta dell'utente gira un job alla volta.
   - **Parallelismo limitato (`--jobs N`)**: l'utente può tenere 2–3 job in parallelo. Le celle si dividono in N
     job sequenziali con celle **disgiunte**, bilanciati sul numero di problemi (LPT), quindi due job non lavorano
     mai sulla stessa cella. Il default è 1.
   - **Due persone (`--share K/M`)**: i due utenti del progetto (cluster_info §2) possono tenere 3 job ciascuno.
     Le celle si dividono prima in M quote bilanciate e deterministiche (lo stesso comando dà le stesse quote a
     chiunque lo lanci) e ognuno invia la propria (`--share 1/2`, `--share 2/2`), eventualmente divisa in
     `--jobs N`. I nomi dei job includono la quota (`wmb-<fase>-s1of2`). Ogni job parte con `umask 002`, così
     i file restano scrivibili dal gruppo `did_tesi_nlp_330`.
   - `bench submit` **rifiuta un nuovo invio** se nell'account di progetto (tutti gli utenti, `squeue -A`) ci
     sono job della stessa fase che possono sovrapporsi: il job senza quota, la stessa quota, oppure quote di una
     divisione diversa, perché un secondo invio rilancerebbe le stesse celle in parallelo e due job scriverebbero lo
     stesso file parziale. Per cambiare il numero di job si cancellano quelli in coda (`scancel`) e si reinvia:
     i problemi completati restano nel file parziale e vengono ripresi. `--allow-concurrent` scavalca il controllo.
   - Se le celle di una fase richiedono profili diversi (es. CPU e GPU), si invia un job per profilo, incatenati
     con `--dependency=afterany`: ne gira sempre uno solo.
   - Si usa **submitit** direttamente, non `hydra-submitit-launcher` (SPEC §16.3): il profilo viene dalla
     `ResourcePolicy` (GPU → `slurm_gpu` su gpuq, CPU → `slurm_cpu` su defq), mentre il launcher ne applica uno
     solo a tutto il multirun. La libreria sottostante è la stessa.
   - Nei job: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`, `HF_HOME`, `HF_HUB_CACHE`,
     `TOKENIZERS_PARALLELISM=false`, `PYTHONHASHSEED=0`.
   - `--wckey` è disattivato.
   - Il job è `Checkpointable`: al segnale che precede il timeout (5,5 h) submitit lo rimette in coda, al massimo
     30 volte. Le 16 celle di L1 non stanno in un solo job. Al riavvio le celle complete si saltano e quella in
     corso riprende dal file parziale.
   - Se il cluster non permettesse la rimessa in coda, basta rilanciare lo stesso `bench submit`: il risultato è
     identico.
   - I log di SLURM vanno in `artifacts/_slurm/<fase>/<job_id>/`.

## Conseguenze

- Cambiare system prompt, template, regola di estrazione o decoding invalida le baseline e ogni confronto
  baseline/marcato: va fatto solo con un nuovo ADR.
- L'impronta del file parziale impedisce di mescolare campioni generati con configurazioni diverse.
