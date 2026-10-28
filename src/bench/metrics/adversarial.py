"""Classificatori avversari dell'impercettibilità (SPEC §13.4; ADR-015).

Positivi: codice marcato; negativi: riferimento dello stesso modello e linguaggio (baseline; per
MCGMark la gemella, con la baseline normale come confronto secondario, TODO 10). Codice vuoto
escluso (D37). Due varianti, con gli stessi fold ``StratifiedGroupKFold(n_splits=5)`` raggruppati
per ``problem_key`` (nessun problema in due fold: :func:`check_folds`):

- ``adversarial_logreg``: regressione logistica su feature esplicite (stile dei nomi, istogramma
  dei tipi di nodo tree-sitter, n-grammi di token 1–3 TF-IDF con vocabolario ≤ 20.000), ``C``
  scelto con una CV interna raggruppata per problema sul fold di training;
- ``adversarial_unixcoder``: ``microsoft/unixcoder-base`` con testa di classificazione, max 512
  token, lr 2e-5, ≤ 3 epoche, batch 16, early stopping sull'AUROC di un 10% interno al fold di
  training (diviso per problema).

L'output di entrambe è la tabella delle predizioni out-of-fold (``sample_id``, ``problem_key``,
``label``, ``score``, ``fold``); le metriche (AUROC medio ± deviazione standard fra i fold, AUROC
sulle predizioni aggregate con IC bootstrap per problema) le calcola la fase
``metrics_imperceptibility``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from bench.domain.enums import Language
from bench.lang.parsers import ParserService, default_parsers

N_SPLITS = 5
INNER_SPLITS = 3
LOGREG_CS = (0.01, 0.1, 1.0, 10.0, 100.0)
MAX_FEATURES = 20_000
UNIXCODER = {"max_length": 512, "lr": 2e-5, "epochs": 3, "batch_size": 16, "val_fraction": 0.1}

Array = npt.NDArray[Any]
Splits = list[tuple[Array, Array]]
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|\S")
_SNAKE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")
_CAMEL = re.compile(r"^[a-z][a-z0-9]*([A-Z][a-z0-9]*)+$")
_PASCAL = re.compile(r"^[A-Z][a-z0-9]+([A-Z][a-z0-9]*)*$")
_IDENTIFIER_TYPES = frozenset(
    {"identifier", "field_identifier", "type_identifier", "property_identifier",
     "shorthand_property_identifier", "namespace_identifier"}
)  # fmt: skip


def code_tokens(code: str) -> list[str]:
    """Token per gli n-grammi: identificatori, numeri e singoli simboli."""
    return _TOKEN.findall(code)


def _walk(code: str, language: Language, parsers: ParserService) -> Iterator[Any]:
    stack = [parsers.parse(code, language).root_node]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def style_features(
    code: str, language: Language, parsers: ParserService | None = None
) -> dict[str, float]:
    """Stile dei nomi e istogramma dei tipi di nodo (frazioni) di un campione."""
    parsers = parsers or default_parsers()
    names: list[str] = []
    types: Counter[str] = Counter()
    for node in _walk(code, language, parsers):
        types[node.type] += 1
        if node.type in _IDENTIFIER_TYPES and node.text is not None:
            names.append(node.text.decode("utf-8", errors="replace"))
    total_nodes = max(1, sum(types.values()))
    n = max(1, len(names))
    out: dict[str, float] = {
        "name_snake": sum(bool(_SNAKE.match(x)) for x in names) / n,
        "name_camel": sum(bool(_CAMEL.match(x)) for x in names) / n,
        "name_pascal": sum(bool(_PASCAL.match(x)) for x in names) / n,
        "name_len_mean": float(np.mean([len(x) for x in names])) if names else 0.0,
    }
    initials = Counter((x[0].lower() if x[0].isalpha() else "_") for x in names if x)
    for letter, count in initials.items():
        out[f"initial_{letter}"] = count / n
    for node_type, count in types.items():
        out[f"node_{node_type}"] = count / total_nodes
    return out


def folds(groups: pd.Series, labels: pd.Series, seed: int) -> Splits:
    """Fold ``StratifiedGroupKFold(n_splits=5, shuffle)`` per problema, controllati."""
    from sklearn.model_selection import StratifiedGroupKFold

    splitter = StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=seed % 2**32)
    out = list(splitter.split(np.zeros(len(labels)), labels.to_numpy(), groups.to_numpy()))
    check_folds(groups, out)
    return out


def check_folds(groups: pd.Series, splits: Splits) -> None:
    """Nessun problema sia nel training sia nel test di un fold, e ogni campione testato una volta.

    Raises:
        ValueError: se i fold si sovrappongono.
    """
    g = groups.to_numpy()
    seen = np.zeros(len(g), dtype=int)
    for k, (train, test) in enumerate(splits):
        overlap = set(g[train]) & set(g[test])
        if overlap:
            raise ValueError(f"fold {k}: problems in train and test: {sorted(overlap)[:5]}")
        seen[test] += 1
    if not (seen == 1).all():
        raise ValueError("each sample must be in exactly one test fold")


def logreg_oof(
    codes: list[str], labels: Array, groups: Array, splits: Splits, language: Language,
    seed: int,
) -> tuple[Array, list[dict[str, Any]]]:  # fmt: skip
    """Predizioni out-of-fold della regressione logistica e, per fold, la ``C`` scelta."""
    from scipy.sparse import csr_matrix, hstack
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedGroupKFold
    from sklearn.preprocessing import MaxAbsScaler

    parsers = default_parsers()
    style = [style_features(c, language, parsers) for c in codes]

    def fit_predict(train: Array, test: Array, c: float) -> Array:
        tfidf = TfidfVectorizer(
            tokenizer=code_tokens, lowercase=False, token_pattern=None, ngram_range=(1, 3),
            max_features=MAX_FEATURES,
        )  # fmt: skip
        dicts = DictVectorizer()
        scaler = MaxAbsScaler()
        x_train = hstack(
            [tfidf.fit_transform([codes[i] for i in train]),
             scaler.fit_transform(dicts.fit_transform([style[i] for i in train]))]
        ).tocsr()  # fmt: skip
        x_test = hstack(
            [tfidf.transform([codes[i] for i in test]),
             scaler.transform(dicts.transform([style[i] for i in test]))]
        ).tocsr()  # fmt: skip
        model = LogisticRegression(C=c, max_iter=5000, solver="liblinear")
        model.fit(csr_matrix(x_train), labels[train])
        return np.asarray(model.predict_proba(x_test)[:, 1])

    scores = np.full(len(codes), np.nan)
    chosen: list[dict[str, Any]] = []
    for k, (train, test) in enumerate(splits):
        inner = StratifiedGroupKFold(n_splits=INNER_SPLITS, shuffle=True, random_state=seed + k)
        best_c, best_auc = LOGREG_CS[0], -1.0
        for c in LOGREG_CS:
            aucs = []
            for itr, ite in inner.split(np.zeros(len(train)), labels[train], groups[train]):
                pred = fit_predict(train[itr], train[ite], c)
                aucs.append(roc_auc_score(labels[train][ite], pred))
            if float(np.mean(aucs)) > best_auc:
                best_c, best_auc = c, float(np.mean(aucs))
        scores[test] = fit_predict(train, test, best_c)
        chosen.append({"fold": k, "C": best_c, "inner_auroc": best_auc})
    return scores, chosen


def unixcoder_oof(
    codes: list[str], labels: Array, groups: Array, splits: Splits, model_path: str,
    seed: int, device: str = "cuda:0",
) -> tuple[Array, list[dict[str, Any]]]:  # fmt: skip
    """Predizioni out-of-fold di UniXcoder con testa di classificazione (fine-tuning per fold)."""
    import torch
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import GroupShuffleSplit
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    hp = UNIXCODER
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    encoded = tokenizer(
        codes, truncation=True, max_length=int(hp["max_length"]), padding="max_length",
        return_tensors="pt",
    )  # fmt: skip
    y = torch.tensor(labels, dtype=torch.long)

    def predict(model: Any, idx: Array) -> Array:
        model.eval()
        out = []
        with torch.no_grad():
            for s in range(0, len(idx), 64):
                b = idx[s : s + 64]
                logits = model(
                    input_ids=encoded["input_ids"][b].to(device),
                    attention_mask=encoded["attention_mask"][b].to(device),
                ).logits
                out.append(torch.softmax(logits.float(), dim=-1)[:, 1].cpu().numpy())
        return np.concatenate(out) if out else np.zeros(0)

    scores = np.full(len(codes), np.nan)
    log: list[dict[str, Any]] = []
    for k, (train, test) in enumerate(splits):
        torch.manual_seed(seed + k)
        split = GroupShuffleSplit(n_splits=1, test_size=float(hp["val_fraction"]),
                                  random_state=seed + k)  # fmt: skip
        fit_i, val_i = next(split.split(train, labels[train], groups[train]))
        fit, val = train[fit_i], train[val_i]
        model = AutoModelForSequenceClassification.from_pretrained(
            model_path, num_labels=2, local_files_only=True
        ).to(device)
        optim = torch.optim.AdamW(model.parameters(), lr=float(hp["lr"]))  # type: ignore[attr-defined]
        rng = np.random.default_rng(seed + k)
        best_auc, best_state, best_epoch = -1.0, None, 0
        for epoch in range(int(hp["epochs"])):
            model.train()
            order = rng.permutation(fit)
            for s in range(0, len(order), int(hp["batch_size"])):
                b = order[s : s + int(hp["batch_size"])]
                loss = model(
                    input_ids=encoded["input_ids"][b].to(device),
                    attention_mask=encoded["attention_mask"][b].to(device),
                    labels=y[b].to(device),
                ).loss
                optim.zero_grad()
                loss.backward()
                optim.step()
            auc = float(roc_auc_score(labels[val], predict(model, val)))
            if auc > best_auc:  # early stopping: si tiene l'epoca migliore sul 10% interno
                best_auc, best_epoch = auc, epoch + 1
                best_state = {n: p.detach().cpu().clone() for n, p in model.state_dict().items()}
        if best_state is not None:
            model.load_state_dict(best_state)
        scores[test] = predict(model, test)
        log.append({"fold": k, "best_epoch": best_epoch, "val_auroc": best_auc})
        del model, optim
        torch.cuda.empty_cache()
    return scores, log
