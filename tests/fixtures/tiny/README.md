# Fixture `tiny` (SPEC §21.5)

Mini-dataset per i test unitari e di fase. I file JSONL sono estratti dai dataset pubblici
(solo i campi usati dai loader) e trasformati in Parquet/JSONL nella disposizione della §8 di
cluster_info da `tests/datafix.py`.

| File | Origine | Contenuto |
|---|---|---|
| `humanevalplus.jsonl` | EvalPlus HumanEvalPlus v0.1.10 (Apache-2.0; HumanEval: MIT) | `HumanEval/0..4` |
| `mbppplus.jsonl` | EvalPlus MbppPlus v0.2.0 (Apache-2.0; MBPP: CC BY 4.0) | `Mbpp/2, 3, 4, 6, 7` |
| `mbpp_original.jsonl` | google-research-datasets/mbpp, `full` (CC BY 4.0) | `task_id` 1–12 con il ruolo del file d'origine |
| `humanevalpack.jsonl` | bigcode/humanevalpack (MIT) | problemi 0–4 in python, js, java, cpp |

CodeSearchNet e The Stack (ad accesso controllato) sono **sintetici**: le funzioni sono generate
da `tests/datafix.py` con lunghezze controllate.
