"""SSH execution engine for node-runner."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

import asyncssh

from config import Config, NodeConfig


class NodeStatus(Enum):
    """Status of a node's execution."""

    PENDING = "pending"
    CONNECTING = "connecting"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class NodeState:
    """Runtime state for a node."""

    config: NodeConfig
    status: NodeStatus = NodeStatus.PENDING
    current_command: str = ""
    current_command_index: int = 0
    output_lines: list[str] = field(default_factory=list)
    error_message: str = ""
    log_file: Path | None = None


# Type alias for output callback
OutputCallback = Callable[[str, str], None]  # (node_name, line) -> None
StatusCallback = Callable[[str, NodeStatus], None]  # (node_name, status) -> None


class Executor:
    """Manages SSH execution across multiple nodes."""

    def __init__(
        self,
        config: Config,
        on_output: OutputCallback | None = None,
        on_status: StatusCallback | None = None,
        enable_logging: bool = True,
    ):
        self.config = config
        self.on_output = on_output
        self.on_status = on_status
        self.enable_logging = enable_logging
        self.states: dict[str, NodeState] = {}
        self._log_dir: Path | None = None

    def _setup_logging(self) -> None:
        """Set up log directory with timestamp."""
        if not self.enable_logging:
            return
        # Check if any node has logging enabled
        if all(node.no_logs for node in self.config.nodes):
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_dir = self.config.log_dir / timestamp
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Copy the source config file to the log directory
        if self.config.source_path and self.config.source_path.exists():
            shutil.copy(self.config.source_path, self._log_dir / "config.yaml")

    def _emit_output(self, node_name: str, line: str) -> None:
        """Emit output line for a node."""
        if node_name in self.states:
            self.states[node_name].output_lines.append(line)

            # Write to log file
            state = self.states[node_name]
            if state.log_file:
                with open(state.log_file, "a") as f:
                    f.write(line + "\n")

        if self.on_output:
            self.on_output(node_name, line)

    def _emit_status(self, node_name: str, status: NodeStatus) -> None:
        """Emit status change for a node."""
        if node_name in self.states:
            self.states[node_name].status = status
        if self.on_status:
            self.on_status(node_name, status)

    async def run_all(self) -> dict[str, NodeState]:
        """Run commands on all nodes in parallel."""
        self._setup_logging()

        # Initialize states
        for node in self.config.nodes:
            # Determine log file: None if logging disabled globally, via CLI, or per-node
            log_file = None
            if self._log_dir and not node.no_logs:
                log_file = self._log_dir / f"{node.name}.log"

            self.states[node.name] = NodeState(
                config=node,
                log_file=log_file,
            )

        # Run all nodes in parallel
        tasks = [self._run_node(node) for node in self.config.nodes]
        await asyncio.gather(*tasks, return_exceptions=True)

        return self.states

    async def _run_node(self, node: NodeConfig) -> None:
        """Run all commands on a single node."""
        state = self.states[node.name]

        self._emit_status(node.name, NodeStatus.CONNECTING)
        self._emit_output(node.name, f"Connecting to {node.user}@{node.host}:{node.port}...")

        try:
            async with asyncssh.connect(
                node.host,
                port=node.port,
                username=node.user,
                client_keys=[str(node.ssh_key)],
                known_hosts=None,  # Skip host key verification for simplicity
            ) as conn:
                self._emit_status(node.name, NodeStatus.RUNNING)
                self._emit_output(node.name, "Connected successfully")

                # Show working directory if specified
                if node.work_dir:
                    self._emit_output(node.name, f"Working directory: {node.work_dir}")

                self._emit_output(node.name, "")

                for i, cmd in enumerate(node.commands):
                    state.current_command = cmd
                    state.current_command_index = i

                    self._emit_output(node.name, f"$ {cmd}")

                    # Prepend cd to work_dir for each command to ensure correct directory
                    actual_cmd = f"cd {node.work_dir} && {cmd}" if node.work_dir else cmd

                    success = await self._run_command(conn, node.name, actual_cmd)

                    if not success and node.stop_on_error:
                        self._emit_status(node.name, NodeStatus.FAILED)
                        state.error_message = f"Command failed: {cmd}"
                        return

                self._emit_output(node.name, "")
                self._emit_output(node.name, "All commands completed")
                self._emit_status(node.name, NodeStatus.SUCCESS)

        except asyncssh.Error as e:
            self._emit_status(node.name, NodeStatus.FAILED)
            state.error_message = f"SSH error: {e}"
            self._emit_output(node.name, f"ERROR: {e}")
        except OSError as e:
            self._emit_status(node.name, NodeStatus.FAILED)
            state.error_message = f"Connection error: {e}"
            self._emit_output(node.name, f"ERROR: {e}")

    async def _run_command(
        self, conn: asyncssh.SSHClientConnection, node_name: str, cmd: str
    ) -> bool:
        """Run a single command and stream output. Returns True if successful."""
        try:
            async with conn.create_process(
                cmd, term_type="xterm", encoding="utf-8"
            ) as proc:
                # Read stdout and stderr concurrently
                async def read_stream(stream, is_stderr: bool = False):
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        line = line.rstrip("\n\r")
                        prefix = "STDERR: " if is_stderr else ""
                        self._emit_output(node_name, f"{prefix}{line}")

                await asyncio.gather(
                    read_stream(proc.stdout),
                    read_stream(proc.stderr, is_stderr=True),
                )

                await proc.wait()
                exit_status = proc.exit_status

                if exit_status != 0:
                    self._emit_output(
                        node_name, f"Command exited with status {exit_status}"
                    )
                    return False

                return True

        except asyncssh.Error as e:
            self._emit_output(node_name, f"Command error: {e}")
            return False
