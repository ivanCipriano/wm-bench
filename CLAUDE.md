# Istruzioni per Claude Code — progetto wm-bench

Prima di qualsiasi lavoro leggi `SPEC.md` e `docs/cluster_info.md`.
Se sono in conflitto, prevale `docs/cluster_info.md` (vedi la sua §1).

## Regole fisse
- Rispondimi sempre in italiano. Identificatori, nomi di file, chiavi di configurazione e log in inglese; docstring e commenti in italiano.
- Lavora una milestone alla volta (SPEC.md §22). Alla fine di ogni milestone fermati e consegna il riepilogo richiesto dalla §0 di SPEC.md.
- Non hai accesso al cluster. Non eseguire comandi pensati per il cluster (sbatch, srun, apptainer, percorsi /mnt/beegfs/...): scrivili per me, completi e pronti da copiare.
- Non modificare mai il contenuto di `third_party/` né le patch già presenti in `patches/`. Nuove modifiche al codice dei metodi solo come nuove patch numerate.
- Non fare mai `git push` e non modificare i remote Git: lo faccio io. Puoi fare commit locali con messaggio `[M<n>] <area>: <descrizione>`.
- Ogni deviazione dal protocollo va registrata in `docs/deviations.md`.
- Se qualcosa è ambiguo o manca un'informazione, chiedimelo invece di decidere da solo.

## Regole per l'integrazione dei metodi (decisioni dell'utente del 6 ottobre 2026)
Da applicare senza chiedere; nell'audit va indicato quale caso si è applicato. Chiedere solo i casi che non rientrano.
- **Discrepanze tra paper e codice:**
  - codice coerente con sé stesso ma diverso dal paper → si segue il codice, si documenta e, se costa poco, si calcola anche la variante del paper come misura secondaria (come lo z-score di STONE);
  - codice incoerente con sé stesso (inserimento e rilevazione non si accordano) → si segue il paper con una patch minima, si registra la deviazione e se ne misura l'effetto (come γ di MCGMark);
  - valori legati al modello degli autori → si adattano ai nostri generatori come adattamento registrato (come il vocabolario di STONE).
- **Linguaggi:** un metodo si esegue sui linguaggi dichiarati dal paper **e** effettivamente gestiti dal codice. Se manca una delle due condizioni, quel linguaggio è `NOT_APPLICABLE`: niente porting, niente nuove regole o parser. Eccezione: se il metodo non dipende in alcun modo dal linguaggio (usa solo tokenizer e logit, come SWEET) si esegue su tutti e quattro, segnalandolo come estensione. Ogni restrizione va registrata in `docs/deviations.md` e motivata nell'audit. Chiedere solo i casi dubbi (es. un linguaggio non nominato dal paper ma apparentemente gestito dal codice).
