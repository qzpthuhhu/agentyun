"""agentyun sync ..."""
import json
import signal
import sys
import time
import click
from rich.console import Console

from ._common import DEFAULT_SERVER, get_config, require_creds, print_success, print_info, print_error


console = Console()


@click.group("sync")
def sync_cmd():
    """Sync local memory with cloud."""
    pass


@sync_cmd.command("push")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def sync_push(server: str | None, data_dir: str | None):
    """Push local unsynced events to cloud."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    pushed = ac.sync.push()
    print_success(f"Pushed {pushed} local events to cloud")


@sync_cmd.command("pull")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def sync_pull(server: str | None, data_dir: str | None):
    """Pull remote events to local."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    pulled = ac.sync.pull()
    print_success(f"Pulled {pulled} remote events")


@sync_cmd.command("once")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def sync_once(server: str | None, data_dir: str | None):
    """Push then pull (one-shot sync)."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)
    result = ac.sync.once()
    print_success(f"Sync complete: pushed={result['pushed']} pulled={result['pulled']}")


@sync_cmd.command("daemon")
@click.option("--start", "action", flag_value="start", default=True)
@click.option("--stop", "action", flag_value="stop")
@click.option("--status", "action", flag_value="status")
@click.option("--push-interval", default=1.0, type=float, help="Seconds between push ticks")
@click.option("--pull-interval", default=5.0, type=float, help="Seconds between pull ticks")
@click.option("--server", default=None)
@click.option("--data-dir", default=None)
def sync_daemon(action: str, push_interval: float, pull_interval: float,
                server: str | None, data_dir: str | None):
    """Manage the background sync daemon."""
    cfg = get_config(server or DEFAULT_SERVER, data_dir)
    ac = require_creds(cfg)

    if action == "start":
        daemon = ac.sync.daemon_start(
            push_interval=push_interval,
            pull_interval=pull_interval,
        )
        print_success(f"Sync daemon started (push={push_interval}s, pull={pull_interval}s)")
        print_info(f"Daemon running in background thread. Use 'agentyun sync daemon --stop' to stop.")
        # If running in foreground terminal, wait for Ctrl+C
        if sys.stdin.isatty():
            print_info("Press Ctrl+C to stop.")
            try:
                while daemon.is_running():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print_info("\nStopping daemon...")
                daemon.stop()
                print_success("Daemon stopped.")

    elif action == "stop":
        stopped = ac.sync.daemon_stop()
        if stopped:
            print_success("Daemon stopped")
        else:
            print_error("No daemon was running")

    elif action == "status":
        s = ac.sync.daemon_status()
        if s is None:
            print_info("No daemon has been started in this process.")
            print_info("Start with: agentyun sync daemon --start")
            return
        import json as _json
        console.print(_json.dumps(s, indent=2, default=str))