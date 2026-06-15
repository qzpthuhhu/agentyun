"""Share API for AgentCloud.

Wraps the v0.3 /v1/share endpoints. Owner-side and consumer-side.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .memory import MemoryItem
from .client import APIError


@dataclass
class ShareInfo:
    share_id: str
    permissions: str
    label: Optional[str]
    expires_at: Optional[datetime]
    created_at: datetime


class _ShareAPI:
    """Owner-side share management."""

    def __init__(self, cloud):
        self._c = cloud

    def create(
        self,
        permissions: str = "read_memory",
        expires_in: Optional[int] = None,
        label: Optional[str] = None,
    ) -> tuple[str, ShareInfo]:
        """Create a share. Returns (raw_token, ShareInfo).

        The raw token is shown ONCE - save it.
        """
        data = self._c._http.post("/share", json={
            "permissions": permissions,
            "expires_in": expires_in,
            "label": label,
        })
        info = ShareInfo(
            share_id=data["share"]["share_id"],
            permissions=data["share"]["permissions"],
            label=data["share"]["label"],
            expires_at=datetime.fromisoformat(
                data["share"]["expires_at"].replace("Z", "+00:00")
            ) if data["share"]["expires_at"] else None,
            created_at=datetime.fromisoformat(
                data["share"]["created_at"].replace("Z", "+00:00")
            ),
        )
        return data["token"], info

    def list(self) -> List[ShareInfo]:
        data = self._c._http.get("/share")
        items = []
        for s in data.get("items", []):
            items.append(ShareInfo(
                share_id=s["share_id"],
                permissions=s["permissions"],
                label=s.get("label"),
                expires_at=datetime.fromisoformat(
                    s["expires_at"].replace("Z", "+00:00")
                ) if s.get("expires_at") else None,
                created_at=datetime.fromisoformat(
                    s["created_at"].replace("Z", "+00:00")
                ),
            ))
        return items

    def revoke(self, share_id: str) -> bool:
        try:
            self._c._http.request("DELETE", f"/share/{share_id}")
            return True
        except APIError:
            return False


class _SharedAPI:
    """Consumer-side: read someone else's memory via a share token.

    No credentials needed - just the share token. Use this to wire your agent
    to consume another agent's experience.
    """

    def __init__(self, token: str, server_url: str, http_client_factory=None):
        import httpx
        from .config import SDKConfig
        from .client import Credentials, AgentCloud, _HTTPClient
        self.token = token
        self.server_url = server_url

        # Build a minimal HTTP wrapper around the share token.
        # We reuse AgentCloud's _HTTPClient by wrapping it in a tiny shim
        # that ignores auth headers (share endpoints don't need a JWT).
        cfg = SDKConfig(server_url=server_url)
        shim = type("Shim", (), {"config": cfg, "_creds": None, "_http": None})()
        self._http = _HTTPClient(shim)  # type: ignore

    def info(self) -> Dict[str, Any]:
        return self._http.get(f"/share/{self.token}/info")

    def timeline(self, limit: int = 50) -> List[MemoryItem]:
        data = self._http.get(f"/share/{self.token}/timeline", params={"limit": limit})
        items = []
        for it in data.get("items", []):
            items.append(MemoryItem(
                event_id=it["event_id"],
                type=it["type"],
                memory_type=it.get("memory_type", "fact"),
                content=it["content"],
                tags=it.get("tags", []),
                meta={},
                created_at=datetime.fromisoformat(
                    it["created_at"].replace("Z", "+00:00")
                ),
            ))
        return items

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        data = self._http.post(
            f"/share/{self.token}/search",
            json={"query": query, "top_k": top_k},
        )
        return data.get("hits", [])