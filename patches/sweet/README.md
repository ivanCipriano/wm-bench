# Patch di SWEET

Submodule: `third_party/sweet-watermark` @ `853b47eb064c180beebd383302d09491fc98a565` (cluster_info §9).

## 0000-modifiche-preesistenti.patch

Modifica preesistente dell'utente (non scritta dall'agente).

| File | Modifica | Motivazione | Impatto |
|---|---|---|---|
| `lm_eval/generation.py` | la condizione del ramo `EXPLogitsProcessor` passa da `args.rdfw or args.srdfw` a `args.exp` | `main.py` definisce `--exp` ma non `--rdfw`/`--srdfw`: al commit fissato quel ramo solleva `AttributeError` | riguarda solo il watermark EXP (baseline del repository), non il processor SWEET; il framework bypassa comunque il loop di `bigcode-evaluation-harness` (SPEC §9.1). Da confermare nell'audit (M6) |

## Verifica

- `scripts/apply_patches.sh sweet` applica la patch a `build/patched/sweet/` (`git apply --check` + `git apply`) sul commit fissato: verificato in M0.
- La fedeltà del comportamento è verificata dal test oracle di SWEET (SPEC §21.4, M6).
