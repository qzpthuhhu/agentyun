"""Agent Cloud Drive Python SDK.

A sync layer for AI agent memory. Key-based identity, append-only event log,
local SQLite WAL for offline-first behavior.
"""
from .client import AgentCloud, AgentCloudError
from .memory import MemoryItem, MemoryType
from .config import SDKConfig
from .daemon import SyncDaemon

__version__ = "0.2.0"

__all__ = [
    "AgentCloud",
    "AgentCloudError",
    "MemoryItem",
    "MemoryType",
    "SDKConfig",
    "SyncDaemon",
]