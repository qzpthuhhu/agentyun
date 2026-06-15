"""SDK configuration."""
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


DEFAULT_DATA_DIR = Path.home() / ".agentcloud"


def _data_dir_default() -> Path:
    env = os.environ.get("AGENTCLOUD_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return DEFAULT_DATA_DIR


class SDKConfig(BaseModel):
    """SDK runtime configuration.

    By default, credentials are stored in ~/.agentcloud/credentials.json
    and local cache in ~/.agentcloud/cache.db (SQLite WAL).

    Override via:
      - AGENTCLOUD_DATA_DIR environment variable
      - AGENTCLOUD_SERVER environment variable
    """

    server_url: str = Field(
        default_factory=lambda: os.environ.get(
            "AGENTCLOUD_SERVER", "http://127.0.0.1:18000"
        )
    )
    data_dir: Path = Field(default_factory=_data_dir_default)
    api_prefix: str = "/v1"
    timeout_seconds: float = 30.0

    class Config:
        arbitrary_types_allowed = True

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    @property
    def cache_db_path(self) -> Path:
        return self.data_dir / "cache.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)