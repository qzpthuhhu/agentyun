"""Memory router: convenience view over the event log + semantic search.

Memory items are derived from events of type 'memory.*' (add/update/delete).
This router provides:
- POST /v1/memory      -> embed + emit a memory.add event (and return event_id)
- GET  /v1/memory      -> list memory items (joined from events)
- POST /v1/memory/search -> semantic search by query (returns top_k items)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import schemas, models
from ..database import get_db
from ..deps import get_current_key
from ..embed import get_embedder, search_vectors as _search_vectors


logger = logging.getLogger("agentyun.memory")
router = APIRouter(prefix="/memory", tags=["memory"])


# event types considered "memory" items
MEMORY_EVENT_TYPES = ("memory.add", "memory.update")


def _store_embedding(payload: dict, vector: list) -> None:
    """Stash the embedding vector inside the event payload."""
    payload["_embedding"] = vector


def _get_embedding(payload: dict) -> Optional[list]:
    return payload.get("_embedding")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    type: Optional[str] = None  # filter by memory type (fact/preference/...)
    tag: Optional[str] = None


class SearchHit(BaseModel):
    event_id: int
    score: float
    content: str
    memory_type: str
    tags: List[str]
    created_at: datetime


class SearchResponse(BaseModel):
    query: str
    hits: List[SearchHit]
    model: str = "all-MiniLM-L6-v2"


@router.post("", response_model=schemas.MemoryAddResponse)
def add_memory(
    req: schemas.MemoryAddRequest,
    db: Session = Depends(get_db),
    current: models.Key = Depends(get_current_key),
):
    """Append a memory.add event to the log.

    Idempotent on client_event_id. Embeds content server-side for semantic search.
    """
    if req.client_event_id:
        existing = db.query(models.Event).filter(
            models.Event.key_id == current.key_id,
            models.Event.client_event_id == req.client_event_id,
        ).first()
        if existing is not None:
            return schemas.MemoryAddResponse(event_id=existing.event_id)

    payload = {
        "content": req.content,
        "type": req.type,
        "tags": req.tags,
        "meta": req.meta,
    }

    # Embed (lazy-loads model on first call; slow first time, fast after)
    try:
        vec = get_embedder().embed_one(req.content)
        _store_embedding(payload, vec)
    except Exception as e:
        # Embedding failure shouldn't block memory writes.
        logger.warning("embedding failed for event: %s", e)

    ev = models.Event(
        key_id=current.key_id,
        type="memory.add",
        payload=payload,
        client_ts=datetime.now(timezone.utc),
        client_event_id=req.client_event_id,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return schemas.MemoryAddResponse(event_id=ev.event_id)


@router.get("", response_model=schemas.MemoryList)
def list_memory(
    limit: int = Query(50, ge=1, le=500),
    type: Optional[str] = Query(None, description="Filter by memory.type (fact/preference/...)"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    db: Session = Depends(get_db),
    current: models.Key = Depends(get_current_key),
):
    """List memory items for the current key (newest first)."""
    q = db.query(models.Event).filter(
        models.Event.key_id == current.key_id,
        models.Event.type.in_(MEMORY_EVENT_TYPES),
    )
    events = q.order_by(desc(models.Event.event_id)).limit(limit).all()

    items: List[schemas.MemoryItem] = []
    for e in events:
        p = e.payload or {}
        if type and p.get("type") != type:
            continue
        tags = p.get("tags", [])
        if tag and tag not in tags:
            continue
        items.append(schemas.MemoryItem(
            event_id=e.event_id,
            type=e.type,
            memory_type=p.get("type", "fact"),
            content=p.get("content", ""),
            tags=tags,
            meta=p.get("meta", {}),
            created_at=e.server_ts,
        ))

    return schemas.MemoryList(items=items, total=len(items))


@router.post("/search", response_model=SearchResponse)
def search_memory(
    req: SearchRequest,
    db: Session = Depends(get_db),
    current: models.Key = Depends(get_current_key),
):
    """Semantic search: embed the query, return top_k memory items by cosine sim.

    v0.2 implementation: scans all memory events for the current key and computes
    cosine similarity in numpy. For datasets >10k events/key, we'll move to
    pgvector / sqlite-vec / faiss in v0.3.
    """
    embedder = get_embedder()

    # Fetch all memory events (could be paginated later)
    events = db.query(models.Event).filter(
        models.Event.key_id == current.key_id,
        models.Event.type.in_(MEMORY_EVENT_TYPES),
    ).all()

    # Build candidate list [(event_id, vector)]
    candidates: List[tuple] = []
    event_lookup = {}
    for e in events:
        p = e.payload or {}
        vec = _get_embedding(p)
        if vec is None:
            # Try to backfill embedding for legacy events
            try:
                vec = embedder.embed_one(p.get("content", ""))
                _store_embedding(p, vec)
                e.payload = p
                db.add(e)  # mark dirty
            except Exception:
                continue
        candidates.append((e.event_id, vec))
        event_lookup[e.event_id] = e

    db.commit()  # persist backfilled embeddings

    if not candidates:
        return SearchResponse(query=req.query, hits=[])

    query_vec = embedder.embed_one(req.query)
    top = _search_vectors(
        np.array(query_vec, dtype=np.float32),
        candidates,
        top_k=req.top_k * 3,  # over-fetch to allow filtering
        min_score=req.min_score,
    )

    hits: List[SearchHit] = []
    for event_id, score in top:
        if len(hits) >= req.top_k:
            break
        e = event_lookup[event_id]
        p = e.payload or {}
        # Filter by type / tag if requested
        if req.type and p.get("type") != req.type:
            continue
        if req.tag and req.tag not in p.get("tags", []):
            continue
        hits.append(SearchHit(
            event_id=event_id,
            score=round(score, 4),
            content=p.get("content", ""),
            memory_type=p.get("type", "fact"),
            tags=p.get("tags", []),
            created_at=e.server_ts,
        ))

    return SearchResponse(query=req.query, hits=hits)