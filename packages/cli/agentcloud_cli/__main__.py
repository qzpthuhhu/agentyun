"""CLI entry point."""
import sys
import click

from .commands import register, login, sync, memory, status, server, share


@click.group()
@click.version_option()
def main():
    """AgentCloud - key-based cloud memory for AI agents.

    Get started:

        agentcloud register --server http://your-server:8000

        agentcloud memory add "用户喜欢简洁回答" --type preference

        agentcloud sync       # on another device, with the same key
    """
    pass


main.add_command(register.register_cmd)
main.add_command(login.login_cmd)
main.add_command(sync.sync_cmd)
main.add_command(memory.memory_cmd)
main.add_command(status.status_cmd)
main.add_command(status.whoami_cmd)
main.add_command(server.server_cmd)
main.add_command(share.share_cmd)


if __name__ == "__main__":
    main()