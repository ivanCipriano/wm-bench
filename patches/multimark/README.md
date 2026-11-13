# Patch di multimark

Submodule: `third_party/multimark` (libreria dei metodi `mcss` e `multimark` della tesi,
repository `MultiMark`; commit fissato in `scripts/setup_submodules.sh` e nel gitlink).

**Nessuna patch, per scelta** (`third_party/multimark/PIPELINE_metodo.md` §0.4): la libreria si
modifica solo nel suo repository e il benchmark aggiorna il puntatore del submodule con un commit
`[P<n>] submodule: multimark → <commit>`. `scripts/apply_patches.sh multimark` copia il submodule
in `build/patched/multimark/` senza applicare nulla e scrive
`build/patched/multimark.source_commit` (che entra nel `config_hash` tramite
`MethodAdapter.source_commit`) e un `build/patched/multimark.patches.sha256` vuoto.

I due metodi del benchmark usano la stessa copia: `configs/method/mcss.yaml` e
`configs/method/multimark.yaml` hanno entrambi `patched_dir: build/patched/multimark`; i worker
mettono `build/patched/multimark` nel `PYTHONPATH` (il pacchetto è `multimark/` alla radice della
copia, quindi `patched_subdir` è vuoto).
