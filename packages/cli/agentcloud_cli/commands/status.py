"""agentcloud status / whoami"""
import json
import click
from rich.console import Console
from rich.table import Table

from ._common import DEFAULT_SERVER, get_config, require_creds


console = Console()


@click.command("status")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def status_cmd(server: str | None, data_dir: str | None):
    """Show sync status (local + remote)."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    s = ac.sync.status()
    table = Table(title="Sync Status")
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    table.add_row("server", s["remote"]["server_url"])
    table.add_row("key_id", s["remote"]["key_id"])
    if s["remote"].get("label"):
        table.add_row("label", s["remote"]["label"])
    table.add_row("local_events", str(s["local"]["total_local_events"]))
    table.add_row("unsynced", str(s["local"]["unsynced"]))
    table.add_row("last_remote_event_id", str(s["local"]["last_remote_event_id"]))
    console.print(table)


@click.command("whoami")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def whoami_cmd(server: str | None, data_dir: str | None):
    """Show current identity."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    me = ac._http.get("/auth/me")
    console.print(f"[bold]key_id:[/bold] {me['key_id']}")
    console.print(f"[bold]label:[/bold] {me.get('label') or '-'}")
    console.print(f"[bold]created:[/bold] {me['created_at']}")