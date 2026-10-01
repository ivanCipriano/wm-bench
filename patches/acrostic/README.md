# Patch di Code Acrostic

Submodule: `third_party/code_acrostic` (repository **originale** `XHLin-gamer/code_acrostic`) @ `83823697f01d6250e7fccd9fa7005bdcfb967ded` (cluster_info §9, §9.1; deviazione D5).

## 0000-aggiunge-CC-json-dal-fork.patch

Aggiunge `CC.json` preso dal fork `xhaughearl/code_acrostic` @ `698479462548ed44919e7eba791298f1a161cc72`. È l'unico file del fork che si usa: `CC.ipynb` del fork è identico all'originale; LICENSE, `.gitattributes`, `.gitignore`, immagini e README del fork sono esclusi.

Contenuto: configurazione in stile MarkLLM (`algorithm_name: CC`, `gamma: 0.1`, `delta: 4.0`, `hash_key`, `prefix_length: 1`, `z_threshold: 1.7`, `f_scheme`, `window_scheme`).

Impatto: nessuna modifica al codice. Se e come `CC.json` venga caricato dal notebook va chiarito nell'audit (M6; cluster_info §11, voce 7). Eventuali modifiche locali a `CC.json` andranno in una patch `0001` (cluster_info §9).

## Verifica

- `scripts/apply_patches.sh acrostic` crea `build/patched/acrostic/CC.json` sul commit fissato: verificato in M0.
