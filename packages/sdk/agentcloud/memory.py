"""Memory domain types."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List


class MemoryType(str, Enum):
    """Semantic type of a memory item."""
    FACT = "fact"
    PREFERENCE = "preference"
    CONVERSATION = "conversation"
    NOTE = "note"
    SKILL = "skill"

    @classmethod
    def coerce(cls, v: Any) -> "MemoryType":
        if isinstance(v, cls):
            return v
        if isinstance(v, str):
            try:
                return cls(v)
            except ValueError:
                return cls.NOTE
        return cls.NOTE


@dataclass
class MemoryItem:
    event_id: int
    type: str  # raw event type: 'memory.add' etc.
    memory_type: str  # the 'type' inside payload: fact/preference/...
    content: str
    tags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = None  # type: ignore