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