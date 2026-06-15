"""agentcloud server ..."""
import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from ._common import print_success, print_info, print_error


console = Console()


REPO_ROOT = Path(__file__).resolve().parents[3]  # agentcloud/packages/cli/agentcloud_cli/commands -> repo root


@click.group("server")
def server_cmd():
    """Manage local cloud server (dev mode)."""
    pass


@server_cmd.command("start")
@click.option("--port", default=18000, help="Server port")
@click.option("--reload/--no-reload", default=True)
def server_start(port: int, reload: bool):
    """Start the cloud server in dev mode (SQLite + local disk)."""
    cloud_dir = REPO_ROOT / "packages" / "cloud"
    if not (cloud_dir / "app" / "main.py").exists():
        print_error(f"Cloud package not found at {cloud_dir}")
        raise click.Abort()
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")
    print_info(f"Starting cloud server on http://127.0.0.1:{port} ...")
    try:
        subprocess.run(cmd, cwd=str(cloud_dir), check=True)
    except KeyboardInterrupt:
        print_info("\nServer stopped.")


@server_cmd.command("status")
def server_status():
    """Check if a local cloud server is responding."""
    import httpx
    try:
        r = httpx.get("http://127.0.0.1:18000/healthz", timeout=2.0)
        if r.status_code == 200:
            print_success(f"Server is up: {r.json()}")
        else:
            print_error(f"Server returned {r.status_code}")
    except Exception as e:
        print_error(f"Server not reachable: {e}")