"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import auth, events, memory, assets, share
from .web import mount_web


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup. Eager-load embedding model in background."""
    init_db()
    # Warm up the embedding model in a thread so first request isn't slow.
    # Lazy import keeps cloud service bootable without sentence-transformers
    # in environments where embedding is disabled.
    try:
        from .embed import get_embedder
        import threading
        threading.Thread(target=get_embedder()._ensure_loaded, daemon=True).start()
    except Exception as e:
        print(f"[startup] embedder warmup skipped: {e}")
    yield


app = FastAPI(
    title="Agent Cloud Drive API",
    version="0.2.0",
    description="Key-based cloud memory layer for AI agents.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "service": settings.service_name,
        "version": "0.2.0",
        "docs": "/docs",
        "web": "/web",
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# Mount v1 routers
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(events.router, prefix=settings.api_prefix)
app.include_router(memory.router, prefix=settings.api_prefix)
app.include_router(assets.router, prefix=settings.api_prefix)
app.include_router(share.router, prefix=settings.api_prefix)

# Mount web UI
mount_web(app)