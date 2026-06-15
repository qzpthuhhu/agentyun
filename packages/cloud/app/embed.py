"""Embedding-based semantic search.

Loads a sentence-transformers model once at startup, embeds memory content,
stores vectors as JSON in the events.payload['_embedding'] field. Search uses
cosine similarity computed in numpy.

For v0.2 we keep it simple:
- All vectors stored in the same SQL row (JSON).
- Search scans all memory events for the key (acceptable up to ~10k events/key).
- For larger scale, switch to pgvector / sqlite-vec / faiss (v0.3).
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional, Tuple

import numpy as np


logger = logging.getLogger("agentyun.embed")


# Model choice: small, fast, good quality for short English+Chinese text
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class Embedder:
    """Thread-safe wrapper around a sentence-transformers model.

    The first call to embed() loads the model (slow). Subsequent calls are fast.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, lazy: bool = True):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        self._lazy = lazy

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            logger.info("loading embedding model: %s", self.model_name)
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._model = SentenceTransformer(self.model_name)
            logger.info("embedding model loaded (dim=%d)", EMBEDDING_DIM)

    def embed(self, texts: List[str]) -> np.ndarray:
        """Embed a list of strings. Returns (N, EMBEDDING_DIM) float32 array."""
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        self._ensure_loaded()
        vectors = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,  # L2-normalized -> dot product = cosine
            show_progress_bar=False,
        )
        return vectors.astype(np.float32)

    def embed_one(self, text: str) -> List[float]:
        """Embed a single string. Returns a list of floats."""
        v = self.embed([text])
        return v[0].tolist()


# Global embedder (lazy-loaded)
_embedder: Optional[Embedder] = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


# ===== Vector math =====

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute cosine similarity row-wise between (N, D) and (M, D) arrays.

    Both inputs are expected to be L2-normalized (which our embed() outputs),
    so cosine = dot product.
    """
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
    return a @ b.T


def search_vectors(
    query_vec: np.ndarray,
    candidates: List[Tuple[int, List[float]]],  # [(event_id, vector), ...]
    top_k: int = 10,
    min_score: float = 0.0,
) -> List[Tuple[int, float]]:
    """Find top_k candidates by cosine similarity to query_vec.

    Returns list of (event_id, score) sorted by score descending.
    """
    if not candidates:
        return []
    ids = [c[0] for c in candidates]
    matrix = np.array([c[1] for c in candidates], dtype=np.float32)
    # matrix is (N, D), query_vec is (1, D) -> similarity (1, N)
    sims = cosine_similarity(query_vec.reshape(1, -1), matrix)[0]
    # Sort by score desc
    order = np.argsort(-sims)
    results = []
    for idx in order:
        score = float(sims[idx])
        if score < min_score:
            break
        results.append((ids[idx], score))
        if len(results) >= top_k:
            break
    return results