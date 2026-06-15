"""HTTP client + high-level AgentCloud facade."""
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

from .config import SDKConfig
from .memory import MemoryItem, MemoryType
from .store import LocalStore


class AgentCloudError(Exception):
    """Base exception for Agent Cloud SDK."""


class AuthError(AgentCloudError):
    """Authentication failed (invalid key, expired token, etc.)."""


class APIError(AgentCloudError):
    """Server returned an error response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


@dataclass
class Credentials:
    key: str
    key_id: str
    access_token: Optional[str] = None
    expires_at: Optional[str] = None
    server_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "key_id": self.key_id,
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "server_url": self.server_url,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Credentials":
        return cls(**d)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        try:
            path.chmod(0o600)
        except Exception:
            pass

    @classmethod
    def load(cls, path: Path) -> Optional["Credentials"]:
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text()))
        except Exception:
            return None


class _MemoryAPI:
    """High-level memory operations."""

    def __init__(self, cloud: "AgentCloud"):
        self._c = cloud

    def add(
        self,
        content: str,
        type: Union[str, MemoryType] = MemoryType.FACT,
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
        client_event_id: Optional[str] = None,
    ) -> int:
        """Append a memory. Returns the remote event_id.

        The event is also written to local WAL for offline-first behavior.
        """
        type_str = MemoryType.coerce(type).value if not isinstance(type, str) else type
        payload = {
            "content": content,
            "type": type_str,
            "tags": tags or [],
            "meta": meta or {},
        }

        # Local WAL first
        local = self._c._store.append(
            type="memory.add",
            payload=payload,
            client_event_id=client_event_id,
        )

        # If a daemon is running, just wake it — it'll handle the push.
        daemon = getattr(self._c, "_daemon", None)
        if daemon is not None and daemon.is_running():
            daemon.wake()
            return local.client_event_id  # remote_id will be set after sync

        # No daemon: do a direct push (synchronous path).
        data = self._c._http.post(
            "/memory",
            json={
                "content": content,
                "type": type_str,
                "tags": tags or [],
                "meta": meta or {},
                "client_event_id": local.client_event_id,
            },
        )
        remote_id = data["event_id"]
        self._c._store.mark_synced([(remote_id, local.client_event_id)])
        return remote_id

    def list(
        self,
        limit: int = 50,
        type: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[MemoryItem]:
        """List memory items (newest first)."""
        params: Dict[str, Any] = {"limit": limit}
        if type:
            params["type"] = type
        if tag:
            params["tag"] = tag
        data = self._c._http.get("/memory", params=params)
        items = []
        for it in data.get("items", []):
            items.append(MemoryItem(
                event_id=it["event_id"],
                type=it["type"],  # event type, e.g. 'memory.add'
                memory_type=it.get("memory_type") or it.get("type", "fact"),
                content=it["content"],
                tags=it.get("tags", []),
                meta=it.get("meta", {}),
                created_at=datetime.fromisoformat(it["created_at"].replace("Z", "+00:00")),
            ))
        return items

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0) -> List[MemoryItem]:
        """Semantic search using server-side embeddings (cosine similarity).

        Falls back to keyword search if the server doesn't support /memory/search.
        """
        try:
            data = self._c._http.post(
                "/memory/search",
                json={"query": query, "top_k": top_k, "min_score": min_score},
            )
        except APIError:
            # Server too old (< v0.2) — fall back to keyword
            return self._keyword_search(query, top_k)

        hits = []
        for h in data.get("hits", []):
            hits.append(MemoryItem(
                event_id=h["event_id"],
                type="memory.add",
                memory_type=h.get("memory_type", "fact"),
                content=h["content"],
                tags=h.get("tags", []),
                meta={},
                created_at=datetime.fromisoformat(h["created_at"].replace("Z", "+00:00")),
            ))
        return hits

    def _keyword_search(self, query: str, top_k: int) -> List[MemoryItem]:
        """Naive keyword fallback when semantic search is unavailable."""
        all_items = self.list(limit=500)
        q = query.lower()
        scored = []
        for it in all_items:
            score = 0
            if q in it.content.lower():
                score += 1
            for tag in it.tags:
                if q in tag.lower():
                    score += 1
            if score > 0:
                scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:top_k]]


class _SyncAPI:
    """Sync engine: pushes local unsynced events, pulls remote updates."""

    def __init__(self, cloud: "AgentCloud"):
        self._c = cloud

    def push(self) -> int:
        """Push all unsynced local events to the server. Returns count pushed."""
        unsynced = self._c._store.unsynced(limit=500)
        if not unsynced:
            return 0
        events = [ev.to_remote_dict() for ev in unsynced]
        data = self._c._http.post("/events", json={"events": events})
        accepted = data["accepted"]
        updates = []
        for ev, rid in zip(unsynced, accepted):
            updates.append((rid, ev.client_event_id))
        self._c._store.mark_synced(updates)
        return len(updates)

    def pull(self) -> int:
        """Pull remote events newer than local cursor. Returns count."""
        cursor = self._c._store.get_cursor()
        data = self._c._http.get("/events", params={"since": cursor, "limit": 500})
        events = data.get("events", [])
        if events:
            new_cursor = max(e["event_id"] for e in events)
            self._c._store.set_cursor(new_cursor)
        return len(events)

    def once(self) -> Dict[str, int]:
        """Push then pull. Returns counts."""
        return {"pushed": self.push(), "pulled": self.pull()}

    def status(self) -> Dict[str, Any]:
        """Show local + remote sync state."""
        local = self._c._store.stats()
        me = self._c._http.get("/auth/me")
        return {
            "local": local,
            "remote": {
                "key_id": me["key_id"],
                "label": me.get("label"),
                "server_url": self._c.config.server_url,
            },
        }

    def daemon_start(self, **kwargs) -> "SyncDaemon":
        """Start a background sync daemon (returns the daemon instance).

        Args:
            push_interval: seconds between push ticks (default 1.0)
            pull_interval: seconds between pull ticks (default 5.0)
            batch_size: max events per push/pull batch (default 100)
            retry_backoff: seconds to wait after a network error (default 5.0)

        The daemon watches the local WAL and pushes changes within ~push_interval
        seconds. It also polls for remote updates every pull_interval seconds.
        """
        from .daemon import SyncDaemon  # local import to avoid cycle

        existing = getattr(self._c, "_daemon", None)
        if existing is not None and existing.is_running():
            return existing
        daemon = SyncDaemon(self._c, **kwargs)
        daemon.start()
        self._c._daemon = daemon  # type: ignore[attr-defined]
        return daemon

    def daemon_stop(self, timeout: float = 5.0) -> bool:
        """Stop the background sync daemon. Returns True if it was running."""
        daemon = getattr(self._c, "_daemon", None)
        if daemon is None:
            return False
        daemon.stop(timeout=timeout)
        return True

    def daemon_status(self) -> Optional[Dict[str, Any]]:
        """Return daemon status (or None if daemon not started)."""
        daemon = getattr(self._c, "_daemon", None)
        if daemon is None:
            return None
        return daemon.status()


class _HTTPClient:
    """Internal HTTP wrapper."""

    def __init__(self, cloud: "AgentCloud"):
        self._c = cloud

    def _url(self, path: str) -> str:
        return self._c.config.server_url.rstrip("/") + self._c.config.api_prefix + path

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._c._creds and self._c._creds.access_token:
            h["Authorization"] = f"Bearer {self._c._creds.access_token}"
        return h

    def request(self, method: str, path: str, **kwargs) -> Any:
        url = self._url(path)
        try:
            resp = httpx.request(
                method, url, headers=self._headers(),
                timeout=self._c.config.timeout_seconds, **kwargs,
            )
        except httpx.RequestError as e:
            raise APIError(0, f"Network error: {e}") from e

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            if resp.status_code == 401:
                raise AuthError(f"Unauthorized: {detail}")
            raise APIError(resp.status_code, str(detail))

        if not resp.content:
            return {}
        return resp.json()

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)


class AgentCloud:
    """Top-level Agent Cloud Drive client.

    Usage:
        # First time: register
        ac = AgentCloud.register(server_url="http://...", label="my-agent")
        # Persist ac.credentials() to ~/.agentyun/credentials.json

        # Subsequent times: login with stored key
        ac = AgentCloud.from_credentials(creds)

        # Or: load from default location
        ac = AgentCloud.load()

        # Use
        ac.memory.add("User likes concise answers", type="preference")
        ac.sync.once()
    """

    def __init__(self, config: SDKConfig, creds: Optional[Credentials] = None):
        self.config = config
        config.ensure_dirs()
        self._creds = creds
        self._store = LocalStore(config.cache_db_path)
        self._http = _HTTPClient(self)
        self.memory = _MemoryAPI(self)
        self.sync = _SyncAPI(self)

    # ===== lifecycle =====

    @classmethod
    def register(
        cls,
        server_url: str,
        label: Optional[str] = None,
        config: Optional[SDKConfig] = None,
    ) -> "AgentCloud":
        """Register a new agent identity on the server.

        Returns an AgentCloud instance with fresh credentials.
        Call .save() to persist credentials to disk.
        """
        config = config or SDKConfig(server_url=server_url)

        # Use raw httpx for the register call (no auth needed)
        resp = httpx.post(
            config.server_url.rstrip("/") + config.api_prefix + "/auth/register",
            json={"label": label} if label else {},
            timeout=config.timeout_seconds,
        )
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        data = resp.json()

        creds = Credentials(
            key=data["key"],
            key_id=data["key_id"],
            server_url=server_url,
        )

        # Auto-login to get access token
        instance = cls(config=config, creds=creds)
        login_resp = httpx.post(
            config.server_url.rstrip("/") + config.api_prefix + "/auth/login",
            json={"key": creds.key},
            timeout=config.timeout_seconds,
        )
        if login_resp.status_code >= 400:
            raise APIError(login_resp.status_code, login_resp.text)
        login_data = login_resp.json()
        creds.access_token = login_data["access_token"]
        creds.expires_at = login_data["expires_at"]
        return instance

    @classmethod
    def from_credentials(
        cls,
        creds: Credentials,
        config: Optional[SDKConfig] = None,
    ) -> "AgentCloud":
        """Construct an AgentCloud from existing credentials.

        Performs a login to fetch a fresh access token.
        """
        config = config or SDKConfig(server_url=creds.server_url or SDKConfig().server_url)

        resp = httpx.post(
            config.server_url.rstrip("/") + config.api_prefix + "/auth/login",
            json={"key": creds.key},
            timeout=config.timeout_seconds,
        )
        if resp.status_code == 401:
            raise AuthError("Invalid key")
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        data = resp.json()

        creds.access_token = data["access_token"]
        creds.expires_at = data["expires_at"]
        creds.key_id = data["key_id"]
        return cls(config=config, creds=creds)

    @classmethod
    def load(cls, config: Optional[SDKConfig] = None) -> Optional["AgentCloud"]:
        """Load credentials from default location (~/.agentyun/credentials.json).

        Returns None if no credentials found.
        """
        config = config or SDKConfig()
        creds = Credentials.load(config.credentials_path)
        if creds is None:
            return None
        return cls.from_credentials(creds, config=config)

    def credentials(self) -> Credentials:
        """Return the current credentials (for persistence)."""
        if self._creds is None:
            raise AgentCloudError("No credentials set")
        return self._creds

    def save(self) -> Path:
        """Save credentials to the configured path. Returns the path."""
        path = self.config.credentials_path
        self.credentials().save(path)
        return path