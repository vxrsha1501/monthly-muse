"""Tone classification with softmax (Section 5.1.1).

    z = e . W + b        P(tone = k | e) = exp(z_k) / sum_j exp(z_j)

A lightweight multinomial logistic regression trained on embeddings of short
messages (data/tone_training.csv). Trained with gradient descent in NumPy -
no sklearn required - and cached as a .npz beside the training file.
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field

import numpy as np

from app.ai.stats import softmax
from app.ai.text import normalise

logger = logging.getLogger("monthlymuse.tone")


@dataclass
class ToneClassifier:
    labels: list[str] = field(default_factory=list)
    W: np.ndarray | None = None          # d x K
    b: np.ndarray | None = None          # K
    dim: int = 384

    @property
    def ready(self) -> bool:
        return self.W is not None and len(self.labels) > 0

    def proba(self, embeddings: np.ndarray) -> np.ndarray:
        """(n x K) probability distribution over tones."""
        if not self.ready:
            n = embeddings.shape[0]
            return np.full((n, len(self.labels) or 1), 1.0 / max(len(self.labels), 1))
        E = np.atleast_2d(np.asarray(embeddings, dtype=np.float64))
        logits = E @ self.W + self.b
        return softmax(logits, axis=1)

    def predict(self, embeddings: np.ndarray) -> list[str]:
        if not self.ready:
            return ["unknown"] * int(np.atleast_2d(embeddings).shape[0])
        P = self.proba(embeddings)
        idx = np.argmax(P, axis=1)
        return [self.labels[i] for i in idx]

    def tone_probability(self, embedding: np.ndarray, tone: str) -> float:
        if not self.ready or tone not in self.labels:
            return 0.5
        P = self.proba(np.atleast_2d(embedding))
        return float(P[0, self.labels.index(tone)])

    # --- training --------------------------------------------------------

    def train(self, texts: list[str], labels: list[str], embedder, *, epochs: int = 60,
              lr: float = 0.35, l2: float = 1e-3, seed: int = 42, verbose: bool = False) -> dict:
        """Softmax cross-entropy trained by full-batch gradient descent."""
        uniq = sorted(set(labels))
        self.labels = uniq
        K = len(uniq)
        label_index = {lab: i for i, lab in enumerate(uniq)}
        E = np.asarray(embedder.embed([normalise(t) for t in texts]), dtype=np.float64)
        self.dim = E.shape[1]
        Y = np.zeros((len(texts), K), dtype=np.float64)
        for i, lab in enumerate(labels):
            Y[i, label_index[lab]] = 1.0

        rng = np.random.default_rng(seed)
        self.W = rng.normal(0, 0.01, size=(self.dim, K))
        self.b = np.zeros(K)
        n = E.shape[0]
        for epoch in range(epochs):
            P = softmax(E @ self.W + self.b, axis=1)
            grad_W = (E.T @ (P - Y)) / n + l2 * self.W
            grad_b = np.mean(P - Y, axis=0)
            self.W -= lr * grad_W
            self.b -= lr * grad_b
            if verbose and epoch % 20 == 0:
                loss = float(-np.mean(np.sum(Y * np.log(P + 1e-12), axis=1)))
                acc = float(np.mean(np.argmax(P, axis=1) == np.argmax(Y, axis=1)))
                logger.info("tone_train epoch=%d loss=%.3f acc=%.3f", epoch, loss, acc)
        P = softmax(E @ self.W + self.b, axis=1)
        acc = float(np.mean(np.argmax(P, axis=1) == np.argmax(Y, axis=1)))
        return {"accuracy": acc, "n": n, "classes": K}

    def save(self, path: str) -> None:
        if not self.ready:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        np.savez_compressed(path, W=self.W, b=self.b, labels=np.array(self.labels))

    @classmethod
    def load(cls, path: str) -> "ToneClassifier":
        if not os.path.exists(path):
            return cls()
        data = np.load(path, allow_pickle=False)
        clf = cls(labels=[str(x) for x in data["labels"]], W=data["W"], b=data["b"], dim=int(data["W"].shape[0]))
        return clf


def load_or_train(csv_path: str, embedder, cache_path: str | None = None, force: bool = False) -> ToneClassifier:
    cache = cache_path or (os.path.splitext(csv_path)[0] + "_model.npz")
    if not force:
        clf = ToneClassifier.load(cache)
        if clf.ready:
            return clf
    if not os.path.exists(csv_path):
        logger.warning("tone_training_data_missing: %s", csv_path)
        return ToneClassifier()
    texts, labels = [], []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("text") and row.get("tone"):
                texts.append(row["text"])
                labels.append(row["tone"])
    if not texts:
        return ToneClassifier()
    clf = ToneClassifier()
    metrics = clf.train(texts, labels, embedder)
    clf.save(cache)
    logger.info("tone_classifier_trained n=%d acc=%.3f", metrics["n"], metrics["accuracy"])
    return clf


def confusion_matrix(true_labels: list[str], pred_labels: list[str], labels: list[str] | None = None) -> dict:
    """K x K confusion matrix for evaluation (Section 16.1)."""
    labels = labels or sorted(set(true_labels) | set(pred_labels))
    index = {lab: i for i, lab in enumerate(labels)}
    K = len(labels)
    M = np.zeros((K, K), dtype=int)
    for t, p in zip(true_labels, pred_labels):
        if t in index and p in index:
            M[index[t], index[p]] += 1
    return {"labels": labels, "matrix": M.tolist()}
