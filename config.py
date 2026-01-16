"""Configuration loader for node-runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Defaults:
    """Default values that can be overridden per node."""

    user: str = "root"
    port: int = 22
    stop_on_error: bool = True
    no_logs: bool = False
    work_dir: str | None = None
    ssh_key: Path = field(default_factory=lambda: Path("~/.ssh/id_rsa").expanduser())
    timeout: int = 30


@dataclass
class NodeConfig:
    """Configuration for a single node."""

    name: str
    host: str
    port: int
    commands: list[str]
    user: str = "root"
    stop_on_error: bool = True
    no_logs: bool = False
    work_dir: str | None = None
    ssh_key: Path = field(default_factory=lambda: Path("~/.ssh/id_rsa").expanduser())
    timeout: int = 30


@dataclass
class Config:
    """Main configuration for the runner."""

    nodes: list[NodeConfig]
    defaults: Defaults = field(default_factory=Defaults)
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    source_path: Path | None = None  # Path to the original config file


def load_config(config_path: str | Path) -> Config:
    """Load and validate configuration from a YAML file."""
    config_path = Path(config_path).resolve()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    config = _parse_config(raw)
    config.source_path = config_path
    return config


def _parse_defaults(raw: dict[str, Any]) -> Defaults:
    """Parse the defaults section."""
    defaults_raw = raw.get("defaults", {})
    ssh_key_str = defaults_raw.get("ssh_key", "~/.ssh/id_rsa")
    return Defaults(
        user=defaults_raw.get("user", "root"),
        port=defaults_raw.get("port", 22),
        stop_on_error=defaults_raw.get("stop_on_error", True),
        no_logs=defaults_raw.get("no_logs", False),
        work_dir=defaults_raw.get("work_dir"),
        ssh_key=Path(ssh_key_str).expanduser(),
        timeout=defaults_raw.get("timeout", 30),
    )


def _parse_config(raw: dict[str, Any]) -> Config:
    """Parse raw YAML data into Config object."""
    # Parse defaults section
    defaults = _parse_defaults(raw)

    # Parse log directory
    log_dir = Path(raw.get("log_dir", "logs")).expanduser().resolve()

    # Parse command groups
    command_groups: dict[str, list[str]] = raw.get("command_groups", {})

    # Parse nodes
    nodes_raw = raw.get("nodes", [])
    if not nodes_raw:
        raise ValueError("No nodes defined in configuration")

    nodes = []
    for node_raw in nodes_raw:
        node = _parse_node(node_raw, command_groups, defaults)
        nodes.append(node)

    return Config(
        nodes=nodes,
        defaults=defaults,
        log_dir=log_dir,
    )


def _parse_node(
    node_raw: dict[str, Any],
    command_groups: dict[str, list[str]],
    defaults: Defaults,
) -> NodeConfig:
    """Parse a single node configuration."""
    name = node_raw.get("name")
    if not name:
        raise ValueError("Node must have a 'name' field")

    host = node_raw.get("host")
    if not host:
        raise ValueError(f"Node '{name}' must have a 'host' field")

    # All these options inherit from defaults if not specified per-node
    port = node_raw.get("port", defaults.port)
    user = node_raw.get("user", defaults.user)
    stop_on_error = node_raw.get("stop_on_error", defaults.stop_on_error)
    no_logs = node_raw.get("no_logs", defaults.no_logs)
    work_dir = node_raw.get("work_dir", defaults.work_dir)
    timeout = node_raw.get("timeout", defaults.timeout)

    # Parse SSH key with expanduser
    ssh_key = defaults.ssh_key
    if "ssh_key" in node_raw:
        ssh_key = Path(node_raw["ssh_key"]).expanduser()

    # Resolve commands - can be group references or direct commands
    commands_raw = node_raw.get("commands", [])
    commands = _resolve_commands(commands_raw, command_groups, name)

    if not commands:
        raise ValueError(f"Node '{name}' must have at least one command")

    return NodeConfig(
        name=name,
        host=host,
        port=port,
        commands=commands,
        user=user,
        stop_on_error=stop_on_error,
        no_logs=no_logs,
        work_dir=work_dir,
        ssh_key=ssh_key,
        timeout=timeout,
    )


def _resolve_commands(
    commands_raw: list[str], command_groups: dict[str, list[str]], node_name: str
) -> list[str]:
    """Resolve command references to actual commands."""
    commands = []

    for cmd in commands_raw:
        if cmd in command_groups:
            # It's a group reference, expand it
            commands.extend(command_groups[cmd])
        else:
            # It's a direct command
            commands.append(cmd)

    return commands
