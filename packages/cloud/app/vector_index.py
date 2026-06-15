"""Vector index abstraction.

v0.3 introduces proper vector indexes:
- numpy: scan all vectors (baseline, no extra deps)
- sqlite_vec: SQLite + sqlite-vec extension (10-100x faster for dev mode)
- pgvector: Postgres + pgvector extension (production)

The index choice is auto-detected from the database URL but can be overridden
with AGENTCLOUD_VECTOR_BACKEND env var.

All implementations expose the same interface:
    index.add(event_id, vector)
    index.search(query_vector, top_k, min_score) -> List[Tuple[event_id, score]]
    index.delete(event_id)  # optional
"""
import logging
import os
from typing import List, Optional, Tuple

import numpy as np


logger = logging.getLogger("agentcloud.vector")


class VectorIndex:
    """Abstract base. Concrete impls: NumpyScanIndex, SqliteVecIndex, PgVectorIndex."""

    name = "abstract"

    def add(self, event_id: int, vector: List[float]) -> None: ...
    def search(
        self, query_vector: List[float], top_k: int = 10, min_score: float = 0.0,
    ) -> List[Tuple[int, float]]: ...
    def delete(self, event_id: int) -> None: ...
    def stats(self) -> dict: ...


# ===== Numpy baseline =====

class NumpyScanIndex(VectorIndex):
    """Scan all vectors in memory. O(N) per query. No persistence."""

    name = "numpy"

    def __init__(self):
        self._vectors: dict[int, np.ndarray] = {}

    def add(self, event_id: int, vector: List[float]) -> None:
        self._vectors[event_id] = np.array(vector, dtype=np.float32)

    def search(
        self, query_vector: List[float], top_k: int = 10, min_score: float = 0.0,
    ) -> List[Tuple[int, float]]:
        if not self._vectors:
            return []
        ids = list(self._vectors.keys())
        matrix = np.stack([self._vectors[i] for i in ids])
        q = np.array(query_vector, dtype=np.float32).reshape(1, -1)
        sims = (matrix @ q.T).flatten()  # both normalized -> cosine = dot
        order = np.argsort(-sims)[:top_k]
        results = []
        for idx in order:
            score = float(sims[idx])
            if score < min_score:
                break
            results.append((ids[idx], score))
        return results

    def delete(self, event_id: int) -> None:
        self._vectors.pop(event_id, None)

    def stats(self) -> dict:
        return {"backend": self.name, "count": len(self._vectors)}


# ===== SQLite + sqlite-vec =====

class SqliteVecIndex(VectorIndex):
    """Use sqlite-vec extension for fast ANN search."""

    name = "sqlite_vec"

    def __init__(self, db_path_or_url: str, dim: int = 384):
        import sqlite_vec
        import sqlite3

        self.dim = dim
        # Accept either a path or a sqlite:/// URL
        if db_path_or_url.startswith("sqlite:///"):
            self.db_path = db_path_or_url.replace("sqlite:///", "", 1)
        else:
            self.db_path = db_path_or_url

        self._sqlite_vec = sqlite_vec
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.row_factory = sqlite3.Row

        # Create the vec table if missing. vec0 is sqlite-vec's virtual table.
        self._conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
            event_id INTEGER PRIMARY KEY,
            embedding FLOAT[{dim}]
        )
        """)
        self._conn.commit()
        logger.info("sqlite-vec index initialized (dim=%d, db=%s)", dim, self.db_path)

    def add(self, event_id: int, vector: List[float]) -> None:
        # Delete first to handle re-adds cleanly
        self._conn.execute(
            "DELETE FROM memory_vec WHERE event_id = ?",
            (event_id,),
        )
        self._conn.execute(
            "INSERT INTO memory_vec (event_id, embedding) VALUES (?, ?)",
            (event_id, serialize_float32(vector)),
        )
        self._conn.commit()

    def search(
        self, query_vector: List[float], top_k: int = 10, min_score: float = 0.0,
    ) -> List[Tuple[int, float]]:
        rows = self._conn.execute(
            """
            SELECT event_id, distance
            FROM memory_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
            """,
            (serialize_float32(query_vector), top_k),
        ).fetchall()
        results = []
        for r in rows:
            # sqlite-vec returns cosine DISTANCE (smaller = closer).
            # Convert to similarity score (larger = closer, 1 - dist for normalized vectors).
            score = 1.0 - float(r["distance"])
            if score < min_score:
                continue
            results.append((int(r["event_id"]), score))
        return results

    def delete(self, event_id: int) -> None:
        self._conn.execute("DELETE FROM memory_vec WHERE event_id = ?", (event_id,))
        self._conn.commit()

    def stats(self) -> dict:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM memory_vec").fetchone()
        return {"backend": self.name, "count": row["c"], "dim": self.dim}


def serialize_float32(vec: List[float]) -> bytes:
    """Serialize a Python list of floats into packed float32 little-endian bytes."""
    import struct
    return struct.pack(f"<{len(vec)}f", *vec)


# ===== Factory =====

_active_index: Optional[VectorIndex] = None


def get_vector_index(dim: int = 384) -> VectorIndex:
    """Get or create the singleton vector index based on config + DB URL."""
    global _active_index
    if _active_index is not None:
        return _active_index

    from .config import settings

    backend = os.environ.get("AGENTCLOUD_VECTOR_BACKEND", "auto").lower()

    if backend == "auto":
        # Auto-pick based on DB URL
        if settings.database_url.startswith("postgresql"):
            backend = "pgvector"
        elif settings.database_url.startswith("sqlite"):
            backend = "sqlite_vec"  # try sqlite-vec by default in dev
        else:
            backend = "numpy"

    if backend == "numpy":
        logger.info("using numpy baseline index (no ANN)")
        _active_index = NumpyScanIndex()
        return _active_index

    if backend == "sqlite_vec":
        try:
            idx = SqliteVecIndex(settings.database_url, dim=dim)
            _active_index = idx
            return _active_index
        except Exception as e:
            logger.warning("sqlite-vec unavailable (%s), falling back to numpy", e)
            _active_index = NumpyScanIndex()
            return _active_index

    if backend == "pgvector":
        try:
            idx = PgVectorIndex(dim=dim)
            _active_index = idx
            return _active_index
        except Exception as e:
            logger.warning("pgvector unavailable (%s), falling back to numpy", e)
            _active_index = NumpyScanIndex()
            return _active_index

    logger.warning("unknown backend %s, using numpy", backend)
    _active_index = NumpyScanIndex()
    return _active_index


# ===== pgvector (lazy import) =====

class PgVectorIndex(VectorIndex):
    """Postgres + pgvector. Requires the pgvector extension to be installed
    on the database and the connection to use psycopg/psycopg2."""

    name = "pgvector"

    def __init__(self, dim: int = 384):
        from pgvector.sqlalchemy import Vector  # type: ignore
        from sqlalchemy import create_engine, text

        from .config import settings

        self.dim = dim
        self.engine = create_engine(settings.database_url)

        with self.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS memory_vec (
                event_id BIGINT PRIMARY KEY,
                embedding vector({dim})
            )
            """))
        logger.info("pgvector index initialized (dim=%d)", dim)

    def add(self, event_id: int, vector: List[float]) -> None:
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO memory_vec (event_id, embedding) VALUES (:id, :vec) "
                     "ON CONFLICT (event_id) DO UPDATE SET embedding = EXCLUDED.embedding"),
                {"id": event_id, "vec": vector},
            )

    def search(
        self, query_vector: List[float], top_k: int = 10, min_score: float = 0.0,
    ) -> List[Tuple[int, float]]:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                SELECT event_id, 1 - (embedding <=> :vec) AS score
                FROM memory_vec
                ORDER BY embedding <=> :vec
                LIMIT :k
                """),
                {"vec": query_vector, "k": top_k},
            ).fetchall()
        results = []
        for r in rows:
            score = float(r.score)
            if score < min_score:
                continue
            results.append((int(r.event_id), score))
        return results

    def delete(self, event_id: int) -> None:
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM memory_vec WHERE event_id = :id"), {"id": event_id})

    def stats(self) -> dict:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT COUNT(*) AS c FROM memory_vec")).fetchone()
        return {"backend": self.name, "count": row.c, "dim": self.dim}