# Patch di Code Acrostic

Submodule: `third_party/code_acrostic` (repository **originale** `XHLin-gamer/code_acrostic`) @ `83823697f01d6250e7fccd9fa7005bdcfb967ded` (cluster_info §9, §9.1; deviazione D5).

## 0000-aggiunge-CC-json-dal-fork.patch

Aggiunge `CC.json` preso dal fork `xhaughearl/code_acrostic` @ `698479462548ed44919e7eba791298f1a161cc72`. È l'unico file del fork che si usa: `CC.ipynb` del fork è identico all'originale; LICENSE, `.gitattributes`, `.gitignore`, immagini e README del fork sono esclusi.

Contenuto: configurazione in stile MarkLLM (`algorithm_name: CC`, `gamma: 0.1`, `delta: 4.0`, `hash_key`, `prefix_length: 1`, `z_threshold: 1.7`, `f_scheme`, `window_scheme`).

Il `CC.json` della cartella di lavoro dell'utente è **identico** a quello del fork: non esiste una patch di modifiche locali (cluster_info §9.1).

Impatto: nessuna modifica al codice. `CC.ipynb` usa `CC.json` (2 occorrenze) e importa MarkLLM, su cui Code Acrostic è costruito (KGW): MarkLLM è il submodule obbligatorio `third_party/MarkLLM` @ `e43009f3d197f8d10e865e2ff731ba1006d1c7d1` (ADR-002).

Punti per l'audit (M6): il notebook cita `config/CC.json` (relativo al clone di MarkLLM), `/content/CC.json` e `/content/white_list4.json`. Al commit fissato né `third_party/code_acrostic` né `third_party/MarkLLM` contengono `white_list4.json` o `config/CC.json`: va chiarito quali file servono, da dove si caricano e come si genera `white_list4.json`.

## Numerazione

Le patch si numerano in ordine di creazione (cluster_info §1). La prossima, `0001`, sarà l'estrazione del notebook in modulo (SPEC §9.4, Milestone 6).

## Verifica

- `scripts/apply_patches.sh acrostic` crea `build/patched/acrostic/CC.json` sul commit fissato: verificato in M0.
