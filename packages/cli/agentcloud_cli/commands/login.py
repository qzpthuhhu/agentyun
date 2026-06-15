"""agentcloud login"""
import click
from rich.console import Console

from agentcloud import AgentCloud
from agentcloud.client import AuthError
from agentcloud.config import SDKConfig

from ._common import DEFAULT_SERVER, get_config, print_success, print_error


console = Console()


@click.command("login")
@click.option("--key", required=True, help="Master key (from register or recovery)")
@click.option("--server", default=None, help="Cloud server URL (default from creds)")
@click.option("--data-dir", default=None, help="Override data directory")
def login_cmd(key: str, server: str | None, data_dir: str | None):
    """Login with a master key.

    Use this on a new device after registering on another.
    """
    # Read existing creds file if any (to get server_url)
    if data_dir:
        cfg = SDKConfig(server_url=server or DEFAULT_SERVER, data_dir=__import__("pathlib").Path(data_dir).expanduser())
    else:
        cfg = SDKConfig(server_url=server or DEFAULT_SERVER)

    # If server wasn't provided, try to use existing creds file
    if not server:
        from agentcloud.client import Credentials
        existing = Credentials.load(cfg.credentials_path)
        if existing and existing.server_url:
            cfg = SDKConfig(server_url=existing.server_url, data_dir=cfg.data_dir)

    from agentcloud.client import Credentials
    creds = Credentials(key=key, key_id="", server_url=cfg.server_url)

    try:
        ac = AgentCloud.from_credentials(creds, config=cfg)
    except AuthError as e:
        print_error(f"Login failed: {e}")
        raise click.Abort()
    except Exception as e:
        print_error(f"Login failed: {e}")
        raise click.Abort()

    ac.save()
    print_success(f"Logged in as key_id={ac.credentials().key_id}")
    print_success(f"Credentials saved to {cfg.credentials_path}")