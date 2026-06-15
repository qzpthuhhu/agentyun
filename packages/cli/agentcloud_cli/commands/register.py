"""agentcloud register"""
import click
from rich.console import Console
from rich.panel import Panel

from agentcloud import AgentCloud

from ._common import DEFAULT_SERVER, get_config, print_success, print_error


console = Console()


@click.command("register")
@click.option("--server", default=DEFAULT_SERVER, help="Cloud server URL")
@click.option("--label", default=None, help="Optional label for this agent")
@click.option("--data-dir", default=None, help="Override data directory")
def register_cmd(server: str, label: str | None, data_dir: str | None):
    """Register a new agent identity.

    Outputs your master key ONCE - save it somewhere safe!
    You'll need this key on every device that wants to sync.
    """
    cfg = get_config(server, data_dir)
    try:
        ac = AgentCloud.register(server, label=label, config=cfg)
    except Exception as e:
        print_error(f"Registration failed: {e}")
        raise click.Abort()

    # Save credentials
    ac.save()
    key_id = ac.credentials().key_id

    # Show the key prominently
    console.print()
    console.print(Panel.fit(
        f"[bold green]✓ Agent registered![/bold green]\n\n"
        f"[bold]Key ID:[/bold]  {key_id}\n"
        f"[bold]Server:[/bold]  {server}\n"
        f"[bold]Saved to:[/bold] {cfg.credentials_path}\n\n"
        f"[bold yellow]⚠ Your master key (SAVE THIS!):[/bold yellow]\n"
        f"[cyan]{ac.credentials().key}[/cyan]",
        title="Agent Cloud Drive",
        border_style="green",
    ))
    console.print()
    console.print("[dim]On another device, run:[/dim]")
    console.print(f"  agentcloud login --key {ac.credentials().key}")
    console.print()