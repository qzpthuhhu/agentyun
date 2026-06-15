"""agentcloud share ..."""
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ._common import DEFAULT_SERVER, get_config, require_creds, print_success, print_error, print_info


console = Console()


@click.group("share")
def share_cmd():
    """Share memory with other agents via tokens."""
    pass


@share_cmd.command("create")
@click.option("--permissions", default="read_memory",
              type=click.Choice(["read", "read_memory", "full"]),
              help="read | read_memory | full")
@click.option("--expires-in", type=int, default=None,
              help="Seconds until expiry (e.g. 86400 for 1 day). Omit for no expiry.")
@click.option("--label", default=None, help="Optional label to identify this share")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def share_create(permissions: str, expires_in: int | None, label: str | None,
                 server: str | None, data_dir: str | None):
    """Create a share token. The raw token is shown ONCE."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    try:
        token, info = ac.share.create(
            permissions=permissions,
            expires_in=expires_in,
            label=label,
        )
    except Exception as e:
        print_error(f"Failed to create share: {e}")
        raise click.Abort()

    expiry = info.expires_at.strftime("%Y-%m-%d %H:%M UTC") if info.expires_at else "never"
    console.print()
    console.print(Panel.fit(
        f"[bold green]✓ Share created[/bold green]\n\n"
        f"[bold]Share ID:[/bold] {info.share_id}\n"
        f"[bold]Permissions:[/bold] {info.permissions}\n"
        f"[bold]Expires:[/bold] {expiry}\n"
        f"[bold]Label:[/bold] {info.label or '-'}\n\n"
        f"[bold yellow]⚠ Share token (SAVE THIS - shown only once!):[/bold yellow]\n"
        f"[cyan]{token}[/cyan]\n\n"
        f"[dim]On another machine:[/dim]\n"
        f"  agentcloud share consume {token}",
        title="Agent Cloud Drive — Share",
        border_style="green",
    ))
    console.print()


@share_cmd.command("list")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def share_list(server: str | None, data_dir: str | None):
    """List shares I've created."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    items = ac.share.list()
    if not items:
        print_info("No active shares.")
        return
    table = Table(title=f"Active Shares ({len(items)})")
    table.add_column("ID", style="dim", width=12)
    table.add_column("Label")
    table.add_column("Permissions")
    table.add_column("Expires")
    for it in items:
        table.add_row(
            it.share_id[:12],
            it.label or "-",
            it.permissions,
            it.expires_at.strftime("%Y-%m-%d %H:%M") if it.expires_at else "never",
        )
    console.print(table)


@share_cmd.command("revoke")
@click.argument("share_id")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def share_revoke(share_id: str, server: str | None, data_dir: str | None):
    """Revoke a share token."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    if ac.share.revoke(share_id):
        print_success(f"Share {share_id} revoked.")
    else:
        print_error(f"Failed to revoke share {share_id}.")


@share_cmd.command("consume")
@click.argument("token")
@click.option("--query", "-q", default=None, help="Optional semantic search query")
@click.option("--limit", default=20, help="Max items for timeline view")
@click.option("--top", default=5, help="Top-K for search")
@click.option("--server", default=None, help="Server URL (default: same as registered)")
def share_consume(token: str, query: str | None, limit: int, top: int, server: str | None):
    """Read someone else's memory via a share token (no credentials needed).

    Examples:
        agentcloud share consume <TOKEN>
        agentcloud share consume <TOKEN> -q "user preferences"
    """
    from agentcloud import AgentCloud
    server = server or DEFAULT_SERVER
    shared = AgentCloud.connect_share(token, server_url=server)

    try:
        info = shared.info()
    except Exception as e:
        print_error(f"Cannot reach share: {e}")
        raise click.Abort()

    console.print(f"[bold]Share:[/bold] {info.get('label') or '(no label)'}")
    console.print(f"[bold]Owner:[/bold] {info['owner'].get('label') or info['owner'].get('key_id', '?')[:8]}")
    console.print(f"[bold]Permissions:[/bold] {info['permissions']}")
    console.print(f"[bold]Expires:[/bold] {info.get('expires_at') or 'never'}")
    console.print()

    if query:
        console.print(f"[bold]Semantic search: \"{query}\"[/bold]")
        hits = shared.search(query, top_k=top)
        if not hits:
            print_info("No hits.")
        for i, h in enumerate(hits, 1):
            console.print(f"  {i}. [{h['memory_type']}] score={h['score']:.3f} {h['content']}")
    else:
        items = shared.timeline(limit=limit)
        if not items:
            print_info("No memory items.")
        table = Table(title=f"Shared Timeline ({len(items)} items)")
        table.add_column("#", style="dim", width=6)
        table.add_column("Type", style="cyan", width=12)
        table.add_column("Content")
        table.add_column("Created", style="dim")
        for it in items:
            table.add_row(
                str(it.event_id),
                it.memory_type,
                it.content[:80] + ("..." if len(it.content) > 80 else ""),
                it.created_at.strftime("%Y-%m-%d %H:%M") if it.created_at else "",
            )
        console.print(table)