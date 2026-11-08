"""Text embeddings for posts and agent interests.

Two providers:
  * HashingEmbedder  - deterministic, dependency-free (numpy only). Feature-hashes
    word unigrams + bigrams into a fixed-size vector and L2-normalises it. Good
    enough to rank "biryani offer" close to "food delivery discount", runs offline,
    and makes tests reproducible.
  * SentenceTransformerEmbedder - optional, semantic (all-MiniLM-L6-v2, 384-d).
    Enabled with EMBEDDER=sentence-transformers when the package is installed.

Both return unit vectors of length DIM so cosine similarity == dot product,
which is exactly what pgvector's `<=>` (cosine distance) operator expects.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from functools import lru_cache
from typing import Protocol, Sequence

import numpy as np

DIM = int(os.getenv("EMBEDDING_DIM", "384"))

_TOKEN = re.compile(r"[a-z0-9]+")

# Small hand-made synonym map so the offline embedder understands the domain.
# (A real deployment uses a learned model; this keeps the demo meaningful.)
_SYNONYMS = {
    "food": ["meal", "restaurant", "dining"],
    "biryani": ["food", "meal"],
    "pizza": ["food", "meal"],
    "grocery": ["groceries", "quickcommerce"],
    "groceries": ["grocery", "quickcommerce"],
    "upi": ["payment", "payments"],
    "cashback": ["discount", "offer"],
    "discount": ["offer", "deal"],
    "deal": ["offer", "discount"],
    "coupon": ["offer", "discount"],
    "train": ["travel", "rail"],
    "flight": ["travel", "air"],
    "travel": ["trip"],
    "outage": ["downtime", "incident"],
    "downtime": ["outage", "incident"],
    "delivery": ["logistics", "shipping"],
    "loan": ["credit", "finance"],
    "credit": ["finance", "loan"],
    "insurance": ["finance", "policy"],
    "movie": ["entertainment", "tickets"],
}

_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is",
    "are", "at", "by", "from", "this", "that", "our", "your", "we", "you", "it",
    "be", "as", "now", "all", "get", "up",
}


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> np.ndarray: ...

    def embed_many(self, texts: Sequence[str]) -> np.ndarray: ...


def _bucket(token: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(token.encode(), digest_size=8).digest()
    idx = int.from_bytes(h[:4], "little") % dim
    sign = 1.0 if h[4] & 1 else -1.0
    return idx, sign


class HashingEmbedder:
    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOP]
        expanded: list[str] = []
        for w in words:
            expanded.append(w)
            # crude stemming: offers -> offer
            if len(w) > 4 and w.endswith("s"):
                expanded.append(w[:-1])
            expanded.extend(_SYNONYMS.get(w, []))
        bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
        return expanded + bigrams

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok in self._tokens(text):
            idx, sign = _bucket(tok, self.dim)
            vec[idx] += sign * (0.5 if "_" in tok else 1.0)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def embed_many(self, texts: Sequence[str]) -> np.ndarray:
        return np.vstack([self.embed(t) for t in texts]) if texts else np.zeros((0, self.dim))


class SentenceTransformerEmbedder:  # pragma: no cover - optional heavy dependency
    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._m = SentenceTransformer(model)
        self.dim = self._m.get_sentence_embedding_dimension()

    def embed(self, text: str) -> np.ndarray:
        return self._m.encode([text], normalize_embeddings=True)[0].astype(np.float32)

    def embed_many(self, texts: Sequence[str]) -> np.ndarray:
        return self._m.encode(list(texts), normalize_embeddings=True).astype(np.float32)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    if os.getenv("EMBEDDER", "hashing") == "sentence-transformers":
        return SentenceTransformerEmbedder()
    return HashingEmbedder()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def blend(vectors: Sequence[np.ndarray], weights: Sequence[float]) -> np.ndarray:
    """Weighted average of unit vectors, re-normalised (used for interest vectors)."""
    if not vectors:
        return np.zeros(DIM, dtype=np.float32)
    acc = np.zeros_like(vectors[0], dtype=np.float32)
    for v, w in zip(vectors, weights):
        acc += w * v
    n = float(np.linalg.norm(acc))
    return acc / n if n > 0 and not math.isnan(n) else acc
