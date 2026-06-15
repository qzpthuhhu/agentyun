"""Web UI routes: simple timeline + semantic search view.

Authentication: query param ?key=<MASTER_KEY> (insecure but simple for v0.2;
v0.3 will switch to session cookies or signed links).
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import models
from ..auth import create_access_token, find_key_by_raw_key, decode_access_token
from ..database import get_db, SessionLocal


router = APIRouter(tags=["web"])

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_current_key_from_query(
    key: Optional[str] = None,
    token: Optional[str] = None,
    db: Session = None,
) -> Optional[models.Key]:
    """Resolve key from ?key= or ?token= query param."""
    if token:
        key_id = decode_access_token(token)
        if key_id:
            k = db.query(models.Key).filter(
                models.Key.key_id == key_id,
                models.Key.revoked_at.is_(None),
            ).first()
            if k:
                return k
    if key:
        k = find_key_by_raw_key(db, key)
        if k:
            return k
    return None


@router.get("/home", response_class=HTMLResponse)
def web_home(
    request: Request,
    key: Optional[str] = None,
    token: Optional[str] = None,
):
    """Landing page: ask for key, or redirect to timeline."""
    if key or token:
        param = "token=" + token if token else "key=" + key
        return RedirectResponse(url=f"/web/timeline?{param}", status_code=302)
    return templates.TemplateResponse(request, "index.html")


@router.get("/timeline", response_class=HTMLResponse)
def web_timeline(
    request: Request,
    key: Optional[str] = None,
    token: Optional[str] = None,
    limit: int = 50,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Timeline view: chronological list of memory events."""
    current = get_current_key_from_query(key, token, db)
    if current is None:
        return RedirectResponse(url="/web", status_code=302)

    # Issue a token (valid for 24h) so URL bar isn't full of key
    short_token, _ = create_access_token(current.key_id, expires_minutes=60 * 24)

    events = db.query(models.Event).filter(
        models.Event.key_id == current.key_id,
        models.Event.type.in_(("memory.add", "memory.update")),
    ).order_by(desc(models.Event.event_id)).limit(limit).all()

    items = []
    for e in events:
        p = e.payload or {}
        if type and p.get("type") != type:
            continue
        if tag and tag not in p.get("tags", []):
            continue
        items.append({
            "event_id": e.event_id,
            "type": p.get("type", "fact"),
            "content": p.get("content", ""),
            "tags": p.get("tags", []),
            "meta": p.get("meta", {}),
            "created_at": e.server_ts,
        })

    # If q is set, run semantic search and put results as a separate section
    hits = []
    if q:
        try:
            from ..embed import get_embedder
            from ..vector_index import get_vector_index
            embedder = get_embedder()
            index = get_vector_index()
            query_vec = embedder.embed_one(q)
            top = index.search(query_vec, top_k=20, min_score=0.0)
            if top:
                event_ids = [eid for eid, _ in top]
                events = db.query(models.Event).filter(
                    models.Event.event_id.in_(event_ids),
                    models.Event.key_id == current.key_id,
                ).all()
                event_by_id = {e.event_id: e for e in events}
                score_by_id = {eid: sc for eid, sc in top}
                for eid in sorted(
                    [eid for eid in event_ids if eid in event_by_id],
                    key=lambda x: -score_by_id[x],
                ):
                    e = event_by_id[eid]
                    p = e.payload or {}
                    hits.append({
                        "event_id": eid,
                        "score": round(score_by_id[eid], 3),
                        "type": p.get("type", "fact"),
                        "content": p.get("content", ""),
                        "tags": p.get("tags", []),
                        "created_at": e.server_ts,
                    })
        except Exception as ex:
            print(f"search failed: {ex}")

    return templates.TemplateResponse(request, "timeline.html", {
        "key_id": current.key_id,
        "label": current.label,
        "items": items,
        "hits": hits,
        "query": q,
        "filter_type": type,
        "filter_tag": tag,
        "token": short_token,
        "stats": {
            "total_items": len(items),
            "hit_count": len(hits),
        },
    })


@router.post("/add", response_class=HTMLResponse)
async def web_add(
    request: Request,
    content: str = Form(...),
    type: str = Form("fact"),
    tags: str = Form(""),
    token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Add a memory item from the web UI (htmx-friendly)."""
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    current = get_current_key_from_query(token=token, db=db)
    if current is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Create event
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    payload = {
        "content": content,
        "type": type,
        "tags": tag_list,
        "meta": {},
    }
    try:
        from ..embed import get_embedder
        vec = get_embedder().embed_one(content)
        payload["_embedding"] = vec
    except Exception:
        pass

    ev = models.Event(
        key_id=current.key_id,
        type="memory.add",
        payload=payload,
        client_ts=datetime.now(timezone.utc),
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)

    # Return rendered event fragment (htmx swaps this in)
    return templates.TemplateResponse(request, "_event.html", {
        "item": {
            "event_id": ev.event_id,
            "type": type,
            "content": content,
            "tags": tag_list,
            "created_at": ev.server_ts,
        },
        "is_new": True,
    })


def mount_web(app):
    """Mount web router + static files onto a FastAPI app. Call from main.py."""
    app.mount("/web/static", StaticFiles(directory=str(STATIC_DIR)), name="web-static")
    app.include_router(router, prefix="/web")