"""agentcloud memory ..."""
import click
from rich.console import Console
from rich.table import Table

from ._common import DEFAULT_SERVER, get_config, require_creds, print_success, print_error


console = Console()


@click.group("memory")
def memory_cmd():
    """Read/write agent memory."""
    pass


@memory_cmd.command("add")
@click.argument("content")
@click.option("--type", "mem_type", default="fact", help="fact|preference|conversation|note|skill")
@click.option("--tag", "tags", multiple=True, help="Tag (can repeat)")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def memory_add(content: str, mem_type: str, tags: tuple, server: str | None, data_dir: str | None):
    """Add a memory item."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    try:
        eid = ac.memory.add(
            content=content,
            type=mem_type,
            tags=list(tags),
        )
    except Exception as e:
        print_error(f"Failed to add memory: {e}")
        raise click.Abort()
    print_success(f"Memory added (event_id={eid})")


@memory_cmd.command("list")
@click.option("--limit", default=20)
@click.option("--type", "mem_type", default=None, help="Filter by memory type")
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def memory_list(limit: int, mem_type: str | None, tag: str | None, server: str | None, data_dir: str | None):
    """List memory items."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    items = ac.memory.list(limit=limit, type=mem_type, tag=tag)
    if not items:
        console.print("[dim]No memory items yet.[/dim]")
        return

    table = Table(title=f"Memory ({len(items)} items)")
    table.add_column("#", style="dim", width=6)
    table.add_column("Type", style="cyan", width=12)
    table.add_column("Content")
    table.add_column("Tags", style="dim")
    table.add_column("Created", style="dim")

    for it in items:
        table.add_row(
            str(it.event_id),
            it.memory_type,
            it.content[:80] + ("..." if len(it.content) > 80 else ""),
            ",".join(it.tags) if it.tags else "",
            it.created_at.strftime("%Y-%m-%d %H:%M") if it.created_at else "",
        )
    console.print(table)


@memory_cmd.command("search")
@click.argument("query")
@click.option("--top", default=5)
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def memory_search(query: str, top: int, server: str | None, data_dir: str | None):
    """Search memory (v0.1: keyword; v0.2: semantic)."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    hits = ac.memory.search(query, top_k=top)
    if not hits:
        console.print(f"[dim]No hits for '{query}'[/dim]")
        return
    console.print(f"[bold]Top {len(hits)} hits for '{query}':[/bold]")
    for i, it in enumerate(hits, 1):
        console.print(f"  {i}. [{it.memory_type}] {it.content}")