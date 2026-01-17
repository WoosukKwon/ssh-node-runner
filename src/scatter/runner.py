#!/usr/bin/env python3
"""Main entry point for Scatter."""

import argparse
import asyncio
import sys
from pathlib import Path

from .config import load_config
from .dashboard import Dashboard
from .executor import Executor, NodeStatus


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run commands on multiple SSH nodes with real-time monitoring"
    )
    parser.add_argument("config", type=Path, help="Path to YAML configuration file")
    parser.add_argument(
        "--key",
        type=Path,
        help="Override SSH key path from config",
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Disable logging to files",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Run with the TUI dashboard",
    )
    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    # Override SSH key if provided (applies to all nodes)
    if args.key:
        key_path = args.key.expanduser()
        config.defaults.ssh_key = key_path
        for node in config.nodes:
            node.ssh_key = key_path

    # Validate all SSH keys exist
    ssh_keys = {node.ssh_key for node in config.nodes}
    for ssh_key in ssh_keys:
        if not ssh_key.exists():
            print(f"Error: SSH key not found: {ssh_key}", file=sys.stderr)
            return 1

    enable_logging = not args.no_logs

    if not args.dashboard:
        # Run without TUI dashboard (default)
        return _run_headless(config, enable_logging)

    # Run with the dashboard
    app = Dashboard(config, enable_logging=enable_logging)
    app.run()

    # Check final status
    if app.executor:
        failed_nodes = [
            name
            for name, state in app.executor.states.items()
            if state.status.value == "failed"
        ]
        if failed_nodes:
            print(f"\nFailed nodes: {', '.join(failed_nodes)}", file=sys.stderr)
            return 1

    return 0


def _run_headless(config, enable_logging: bool) -> int:
    """Run executor without TUI dashboard."""
    # ANSI colors for different nodes
    colors = [
        "\033[36m",  # Cyan
        "\033[33m",  # Yellow
        "\033[35m",  # Magenta
        "\033[32m",  # Green
        "\033[34m",  # Blue
        "\033[91m",  # Light Red
        "\033[96m",  # Light Cyan
        "\033[93m",  # Light Yellow
    ]
    reset = "\033[0m"

    # Assign colors to nodes
    node_colors = {
        node.name: colors[i % len(colors)]
        for i, node in enumerate(config.nodes)
    }

    def on_output(node_name: str, line: str) -> None:
        color = node_colors.get(node_name, "")
        print(f"{color}[{node_name}]{reset} {line}")

    def on_status(node_name: str, status: NodeStatus) -> None:
        color = node_colors.get(node_name, "")
        print(f"{color}[{node_name}]{reset} Status: {status.value}")

    executor = Executor(
        config,
        on_output=on_output,
        on_status=on_status,
        enable_logging=enable_logging,
    )

    states = asyncio.run(executor.run_all())

    # Check final status
    failed_nodes = [
        name for name, state in states.items() if state.status == NodeStatus.FAILED
    ]
    if failed_nodes:
        print(f"\nFailed nodes: {', '.join(failed_nodes)}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
