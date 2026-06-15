"""Helpers for CLI commands."""
import os
from pathlib import Path

import click
from rich.console import Console

from agentcloud import SDKConfig


console = Console()


DEFAULT_SERVER = os.environ.get("AGENTCLOUD_SERVER", "http://127.0.0.1:18000")


def get_config(server: str, data_dir: str | None = None) -> SDKConfig:
    cfg = SDKConfig(server_url=server)
    if data_dir:
        cfg = SDKConfig(server_url=server, data_dir=Path(data_dir).expanduser())
    return cfg


def require_creds(cfg: SDKConfig):
    """Try to load credentials. Fail with helpful message if missing."""
    from agentcloud import AgentCloud
    from agentcloud.client import AuthError

    ac = AgentCloud.load(cfg)
    if ac is None:
        raise click.ClickException(
            f"No credentials found at {cfg.credentials_path}.\n"
            "Run: agentcloud register  or  agentcloud login --key <KEY>"
        )
    return ac


def print_error(msg: str):
    console.print(f"[bold red]error:[/bold red] {msg}")


def print_success(msg: str):
    console.print(f"[bold green]✓[/bold green] {msg}")


def print_info(msg: str):
    console.print(f"[dim]{msg}[/dim]")