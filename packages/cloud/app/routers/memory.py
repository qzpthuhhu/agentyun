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
from ..embed import get_embedder
from ..vector_index import get_vector_index


logger = logging.getLogger("agentcloud.memory")
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

    # Also write to the vector index for fast ANN search.
    try:
        if "_embedding" in payload:
            get_vector_index().add(ev.event_id, payload["_embedding"])
    except Exception as e:
        logger.warning("vector index write failed for event %d: %s", ev.event_id, e)

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
    """Semantic search using the configured vector index.

    v0.3: uses VectorIndex (auto-detected: numpy | sqlite_vec | pgvector).
    All event_ids in the index belong to this user — we still post-filter by
    key_id from the events table to be safe.
    """
    embedder = get_embedder()
    index = get_vector_index()

    # Embed the query
    query_vec = embedder.embed_one(req.query)

    # Query the vector index (returns event_id, score)
    # Over-fetch to allow for post-filter by type/tag
    top = index.search(query_vec, top_k=req.top_k * 5, min_score=req.min_score)
    if not top:
        return SearchResponse(query=req.query, hits=[])

    # Filter by ownership + load event details
    event_ids = [eid for eid, _ in top]
    events = db.query(models.Event).filter(
        models.Event.event_id.in_(event_ids),
        models.Event.key_id == current.key_id,
        models.Event.type.in_(MEMORY_EVENT_TYPES),
    ).all()
    event_by_id = {e.event_id: e for e in events}
    score_by_id = {eid: sc for eid, sc in top}

    hits: List[SearchHit] = []
    # Re-sort by score desc, only including events we own
    ordered = sorted(
        [eid for eid in event_ids if eid in event_by_id],
        key=lambda eid: -score_by_id[eid],
    )
    for event_id in ordered:
        if len(hits) >= req.top_k:
            break
        e = event_by_id[event_id]
        p = e.payload or {}
        if req.type and p.get("type") != req.type:
            continue
        if req.tag and req.tag not in p.get("tags", []):
            continue
        hits.append(SearchHit(
            event_id=event_id,
            score=round(score_by_id[event_id], 4),
            content=p.get("content", ""),
            memory_type=p.get("type", "fact"),
            tags=p.get("tags", []),
            created_at=e.server_ts,
        ))

    return SearchResponse(query=req.query, hits=hits)