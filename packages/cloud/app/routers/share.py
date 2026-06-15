"""Share router: sub-key / share token for read-only access to memory.

Use cases:
- "Share my agent's memory with colleague's agent" — issue a share token
- "Let a third-party app read (not write) my memory" — read-only token
- "Time-limited access" — set expires_at

Permissions:
- read: can read memory list + events
- read_memory: can read AND semantic-search memory (default)
- full: read + memory + assets

Token format: random URL-safe string (32 bytes, ~43 chars). Stored hashed.
"""
import json
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import schemas, models
from ..auth import create_access_token
from ..database import get_db
from ..deps import get_current_key
from ..embed import get_embedder
from ..vector_index import get_vector_index


router = APIRouter(prefix="/share", tags=["share"])


VALID_PERMISSIONS = ("read", "read_memory", "full")


# ===== Helpers =====

def _gen_share_token() -> str:
    """Generate a URL-safe random token (~43 chars)."""
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _find_share(db: Session, token: str) -> Optional[models.Share]:
    """Find a non-revoked, non-expired share by raw token."""
    token_hash = _hash_token(token)
    share = db.query(models.Share).filter(
        models.Share.token_hash == token_hash,
        models.Share.revoked_at.is_(None),
    ).first()
    if share is None:
        return None
    # Check expiry (handle naive vs aware datetimes from SQLite)
    if share.expires_at:
        now = datetime.now(timezone.utc)
        exp = share.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            return None
    return share


def _resolve_key_from_share(db: Session, token: str) -> Optional[models.Key]:
    """Resolve the parent key via a share token."""
    share = _find_share(db, token)
    if share is None:
        return None
    return db.query(models.Key).filter(
        models.Key.key_id == share.parent_key_id,
        models.Key.revoked_at.is_(None),
    ).first()


# ===== Schemas =====

class ShareCreateRequest(BaseModel):
    permissions: str = Field(default="read_memory")
    expires_in: Optional[int] = Field(
        default=None,
        description="Seconds until expiry (None = never). Max 365 days.",
    )
    label: Optional[str] = None


class ShareOut(BaseModel):
    share_id: str
    permissions: str
    label: Optional[str]
    expires_at: Optional[datetime]
    created_at: datetime
    share_url: Optional[str] = None  # filled only on creation


class ShareCreateResponse(BaseModel):
    share: ShareOut
    token: str = Field(..., description="The raw share token. SHOWN ONCE.")


class ShareList(BaseModel):
    items: List[ShareOut]


# ===== Routes (owner-side) =====

@router.post("", response_model=ShareCreateResponse)
def create_share(
    req: ShareCreateRequest,
    db: Session = Depends(get_db),
    current: models.Key = Depends(get_current_key),
):
    """Create a share token. The raw token is shown ONCE in the response."""
    if req.permissions not in VALID_PERMISSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid permissions. Must be one of: {VALID_PERMISSIONS}",
        )
    if req.expires_in is not None:
        if req.expires_in < 60:
            raise HTTPException(status_code=400, detail="expires_in must be >= 60s")
        if req.expires_in > 365 * 24 * 3600:
            raise HTTPException(status_code=400, detail="expires_in must be <= 365 days")

    token = _gen_share_token()
    expires_at = None
    if req.expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=req.expires_in)

    share = models.Share(
        parent_key_id=current.key_id,
        token_hash=_hash_token(token),
        permissions=req.permissions,
        expires_at=expires_at,
        label=req.label,
    )
    db.add(share)
    db.commit()
    db.refresh(share)

    return ShareCreateResponse(
        token=token,
        share=ShareOut(
            share_id=share.share_id,
            permissions=share.permissions,
            label=share.label,
            expires_at=share.expires_at,
            created_at=share.created_at,
        ),
    )


@router.get("", response_model=ShareList)
def list_shares(
    db: Session = Depends(get_db),
    current: models.Key = Depends(get_current_key),
):
    """List all shares I've created (revoked ones are filtered out by default)."""
    rows = db.query(models.Share).filter(
        models.Share.parent_key_id == current.key_id,
        models.Share.revoked_at.is_(None),
    ).order_by(desc(models.Share.created_at)).all()
    return ShareList(items=[
        ShareOut(
            share_id=s.share_id,
            permissions=s.permissions,
            label=s.label,
            expires_at=s.expires_at,
            created_at=s.created_at,
        )
        for s in rows
    ])


@router.delete("/{share_id}")
def revoke_share(
    share_id: str,
    db: Session = Depends(get_db),
    current: models.Key = Depends(get_current_key),
):
    """Revoke a share token (subsequent accesses fail)."""
    share = db.query(models.Share).filter(
        models.Share.share_id == share_id,
        models.Share.parent_key_id == current.key_id,
        models.Share.revoked_at.is_(None),
    ).first()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")
    share.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "revoked", "share_id": share_id}


# ===== Share-based access (consumer-side) =====

class SharedMemoryItem(BaseModel):
    event_id: int
    type: str
    memory_type: str
    content: str
    tags: List[str]
    created_at: datetime


class SharedTimeline(BaseModel):
    key_id: str = Field(..., description="The owning key's ID")
    label: Optional[str]
    permissions: str
    items: List[SharedMemoryItem]
    total: int


class SharedSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class SharedSearchHit(BaseModel):
    event_id: int
    score: float
    content: str
    memory_type: str


class SharedSearchResponse(BaseModel):
    query: str
    hits: List[SharedSearchHit]


@router.get("/{token}/info")
def share_info(
    token: str,
    db: Session = Depends(get_db),
):
    """Public info about a share (no auth required)."""
    share = _find_share(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found or expired")
    owner = db.query(models.Key).filter(models.Key.key_id == share.parent_key_id).first()
    return {
        "share_id": share.share_id,
        "permissions": share.permissions,
        "label": share.label,
        "expires_at": share.expires_at,
        "created_at": share.created_at,
        "owner": {
            "key_id": owner.key_id if owner else None,
            "label": owner.label if owner else None,
        },
    }


@router.get("/{token}/timeline", response_model=SharedTimeline)
def share_timeline(
    token: str,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Read the owner's memory via a share token (no auth header required)."""
    share = _find_share(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found or expired")
    if share.permissions == "full":
        pass  # all perms
    elif share.permissions in ("read", "read_memory"):
        pass  # ok
    else:
        raise HTTPException(status_code=403, detail="Permission denied")

    owner = db.query(models.Key).filter(models.Key.key_id == share.parent_key_id).first()
    if owner is None:
        raise HTTPException(status_code=404, detail="Owner not found")

    events = db.query(models.Event).filter(
        models.Event.key_id == owner.key_id,
        models.Event.type.in_(("memory.add", "memory.update")),
    ).order_by(desc(models.Event.event_id)).limit(limit).all()

    items = []
    for e in events:
        p = e.payload or {}
        items.append(SharedMemoryItem(
            event_id=e.event_id,
            type=e.type,
            memory_type=p.get("type", "fact"),
            content=p.get("content", ""),
            tags=p.get("tags", []),
            created_at=e.server_ts,
        ))

    return SharedTimeline(
        key_id=owner.key_id,
        label=owner.label,
        permissions=share.permissions,
        items=items,
        total=len(items),
    )


@router.post("/{token}/search", response_model=SharedSearchResponse)
def share_search(
    token: str,
    req: SharedSearchRequest,
    db: Session = Depends(get_db),
):
    """Semantic search against owner's memory via a share token."""
    share = _find_share(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found or expired")
    if share.permissions not in ("read_memory", "full"):
        raise HTTPException(status_code=403, detail="This share does not allow search")

    owner = db.query(models.Key).filter(models.Key.key_id == share.parent_key_id).first()
    if owner is None:
        raise HTTPException(status_code=404, detail="Owner not found")

    embedder = get_embedder()
    index = get_vector_index()

    query_vec = embedder.embed_one(req.query)
    top = index.search(query_vec, top_k=req.top_k * 3, min_score=0.0)
    if not top:
        return SharedSearchResponse(query=req.query, hits=[])

    event_ids = [eid for eid, _ in top]
    events = db.query(models.Event).filter(
        models.Event.event_id.in_(event_ids),
        models.Event.key_id == owner.key_id,
        models.Event.type.in_(("memory.add", "memory.update")),
    ).all()
    event_by_id = {e.event_id: e for e in events}
    score_by_id = {eid: sc for eid, sc in top}

    hits = []
    ordered = sorted(
        [eid for eid in event_ids if eid in event_by_id],
        key=lambda eid: -score_by_id[eid],
    )
    for eid in ordered[:req.top_k]:
        e = event_by_id[eid]
        p = e.payload or {}
        hits.append(SharedSearchHit(
            event_id=eid,
            score=round(score_by_id[eid], 4),
            content=p.get("content", ""),
            memory_type=p.get("type", "fact"),
        ))
    return SharedSearchResponse(query=req.query, hits=hits)