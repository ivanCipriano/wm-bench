# Patch delle dipendenze degli ambienti (solo documentazione)

Documentano le modifiche a `setup.py` con cui `human-eval` e `mxeval` sono stati installati in modalità editable negli ambienti dei metodi (cluster_info §1, §3, §9.2). Gli ambienti sono già installati: `scripts/apply_patches.sh` **non** applica queste patch.

| Dipendenza | Commit | Ambienti | Patch |
|---|---|---|---|
| human-eval | `6d43fb980f9fee3c892a914eda09951f772ad10d` | `code_acrostic` | `human-eval/0000-setup.patch` (solo `setup.py`) |
| mxeval | `e09974f990eeaf0c0e8f2b5eaff4be66effb2c86` | `acw`, `promptmark` | `mxeval/0000-setup.patch` (solo `setup.py`) |

Le cartelle installate sono in `wm_bench/deps/` e non vanno spostate né cancellate.
