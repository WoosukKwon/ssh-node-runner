"""TUI Dashboard for node-runner."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, RichLog, Static
from textual.worker import Worker

from config import Config
from executor import Executor, NodeStatus

if TYPE_CHECKING:
    from executor import NodeState


STATUS_ICONS = {
    NodeStatus.PENDING: ("", "dim"),
    NodeStatus.CONNECTING: ("", "yellow"),
    NodeStatus.RUNNING: ("", "yellow"),
    NodeStatus.SUCCESS: ("", "green"),
    NodeStatus.FAILED: ("", "red"),
}


class NodePanel(Static):
    """A panel displaying output for a single node."""

    status: reactive[NodeStatus] = reactive(NodeStatus.PENDING)

    def __init__(self, node_name: str, host: str, port: int, user: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.node_name = node_name
        self.host = host
        self.port = port
        self.user = user

    def compose(self) -> ComposeResult:
        yield Label(self._get_header(), id=f"header-{self.node_name}")
        yield RichLog(
            id=f"log-{self.node_name}",
            highlight=True,
            markup=True,
            wrap=True,
            auto_scroll=True,
        )

    def _get_header(self) -> str:
        icon, color = STATUS_ICONS.get(self.status, ("?", "white"))
        return f"[{color}]{icon}[/] [{color}][bold]{self.node_name}[/bold][/] [{color}]{self.user}@{self.host}:{self.port}[/]"

    def watch_status(self, status: NodeStatus) -> None:
        """Update header when status changes."""
        if not self.is_mounted:
            return
        header = self.query_one(f"#header-{self.node_name}", Label)
        header.update(self._get_header())

    def append_output(self, line: str) -> None:
        """Append a line of output to this panel."""
        log = self.query_one(f"#log-{self.node_name}", RichLog)
        if line.startswith("$ "):
            log.write(f"[bold cyan]{line}[/bold cyan]")
        elif line.startswith("STDERR:"):
            log.write(f"[red]{line}[/red]")
        elif line.startswith("ERROR:"):
            log.write(f"[bold red]{line}[/bold red]")
        elif "completed" in line.lower():
            log.write(f"[green]{line}[/green]")
        else:
            log.write(line)


class StatusBar(Static):
    """Bottom status bar showing overall progress."""

    completed: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    running: reactive[bool] = reactive(True)

    def render(self) -> str:
        status = "Running..." if self.running else "Complete"
        return f"Progress: {self.completed}/{self.total} nodes complete | {status} | Press 'q' to quit"


@dataclass
class NodeOutput(Message):
    """Message for node output."""
    node_name: str
    line: str


@dataclass
class NodeStatusChange(Message):
    """Message for node status change."""
    node_name: str
    status: NodeStatus


class Dashboard(App):
    """Main TUI Dashboard application."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-gutter: 1;
    }

    NodePanel {
        border: solid $primary;
        height: 100%;
        min-height: 10;
    }

    NodePanel Label {
        dock: top;
        padding: 0 1;
        background: $surface;
    }

    NodePanel RichLog {
        height: 1fr;
        padding: 0 1;
    }

    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 1;
    }

    #node-container {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
    ]

    def __init__(self, config: Config, enable_logging: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.enable_logging = enable_logging
        self.panels: dict[str, NodePanel] = {}
        self.executor: Executor | None = None
        self._worker: Worker | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # Create panels for each node
        for node in self.config.nodes:
            panel = NodePanel(
                node.name,
                node.host,
                node.port,
                node.user,
                id=f"panel-{node.name}",
            )
            self.panels[node.name] = panel
            yield panel

        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Start execution when the app mounts."""
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.total = len(self.config.nodes)

        # Create executor with callbacks
        self.executor = Executor(
            self.config,
            on_output=self._on_output,
            on_status=self._on_status,
            enable_logging=self.enable_logging,
        )

        # Start execution using Textual's worker system
        self._worker = self.run_worker(self._run_execution(), exclusive=True, thread=True)

    async def _run_execution(self) -> None:
        """Run the executor and update UI when complete."""
        if self.executor:
            await self.executor.run_all()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker completion."""
        if event.worker == self._worker and event.state == event.worker.state.SUCCESS:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.running = False

    def _on_output(self, node_name: str, line: str) -> None:
        """Handle output from a node - posts message to main thread."""
        self.post_message(NodeOutput(node_name, line))

    def _on_status(self, node_name: str, status: NodeStatus) -> None:
        """Handle status change for a node - posts message to main thread."""
        self.post_message(NodeStatusChange(node_name, status))

    def on_node_output(self, message: NodeOutput) -> None:
        """Handle NodeOutput message in main thread."""
        if message.node_name in self.panels:
            self.panels[message.node_name].append_output(message.line)

    def on_node_status_change(self, message: NodeStatusChange) -> None:
        """Handle NodeStatusChange message in main thread."""
        if message.node_name in self.panels:
            self.panels[message.node_name].status = message.status

        # Update completed count
        if message.status in (NodeStatus.SUCCESS, NodeStatus.FAILED):
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.completed += 1

    async def action_quit(self) -> None:
        """Quit the application."""
        if self._worker and self._worker.is_running:
            self._worker.cancel()
        self.exit()
