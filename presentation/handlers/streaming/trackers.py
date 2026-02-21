"""
Progress and status trackers for streaming operations.

Includes:
- ProgressTracker: Multi-step operation progress
- HeartbeatTracker: Periodic status updates with spinner
- FileChange/FileChangeTracker: Track file modifications
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from presentation.handlers.streaming.handler import StreamingHandler

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Track progress of multi-step operations"""

    def __init__(self, streaming_handler: "StreamingHandler"):
        self.handler = streaming_handler
        self.steps: list[str] = []
        self.current_step = 0
        self.total_steps = 0

    async def set_steps(self, steps: list[str]):
        """Set the steps for this operation"""
        self.steps = steps
        self.total_steps = len(steps)
        self.current_step = 0
        await self._update_progress()

    async def advance(self):
        """Move to the next step"""
        self.current_step = min(self.current_step + 1, self.total_steps)
        await self._update_progress()

    async def complete_step(self, step_index: int):
        """Mark a specific step as complete"""
        self.current_step = max(self.current_step, step_index + 1)
        await self._update_progress()

    async def _update_progress(self):
        """Update the progress display"""
        if not self.steps:
            return

        progress_lines = []
        for i, step in enumerate(self.steps):
            if i < self.current_step:
                progress_lines.append(f"✅ {step}")
            elif i == self.current_step:
                progress_lines.append(f"⏳ {step}")
            else:
                progress_lines.append(f"⬜ {step}")

        progress_text = "\n".join(progress_lines)
        await self.handler.set_status(f"Progress ({self.current_step}/{self.total_steps})")


class HeartbeatTracker:
    """Periodic status updates during long operations.

    Shows elapsed time and current action with animated spinner.
    Interval = 2 seconds, synchronized with coordinator.
    """

    # Braille spinner animation (smooth rotating dots)
    SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    # Action-specific emojis (static, one per action)
    ACTION_EMOJIS = {
        "thinking": "🧠",
        "reading": "📖",
        "writing": "✍️",
        "editing": "✏️",
        "searching": "🔎",
        "executing": "⚡",
        "planning": "🎯",
        "analyzing": "🔬",
        "waiting": "⏳",
        "default": "🤖",
    }

    # Action labels in Russian
    ACTION_LABELS = {
        "thinking": "Think",
        "reading": "I'm reading",
        "writing": "I'm writing",
        "editing": "Editing",
        "searching": "looking for",
        "executing": "Executing",
        "planning": "I'm planning",
        "analyzing": "Analyzing",
        "waiting": "I'm waiting for an answer",
        "default": "Working",
    }

    # Interval heartbeat = 2 seconds (synchronized with coordinator)
    DEFAULT_INTERVAL = 2.0

    def __init__(self, streaming: "StreamingHandler", interval: float = DEFAULT_INTERVAL):
        self.streaming = streaming
        self.interval = max(interval, self.DEFAULT_INTERVAL)  # No less 2 seconds!
        self.start_time = time.time()
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._spinner_idx = 0
        self._current_action = "default"
        self._action_detail = ""  # Additional detail like filename

    def set_action(self, action: str, detail: str = ""):
        """Set current action being performed.

        Args:
            action: One of: thinking, reading, writing, editing, searching,
                   executing, planning, analyzing, waiting, default
            detail: Optional detail like filename (will be truncated)
        """
        if action in self.ACTION_EMOJIS:
            self._current_action = action
        else:
            self._current_action = "default"

        # Truncate detail to keep status line short
        if detail:
            if len(detail) > 30:
                detail = "..." + detail[-27:]
            self._action_detail = detail
        else:
            self._action_detail = ""

    async def start(self):
        """Start heartbeat updates"""
        self.is_running = True
        self.start_time = time.time()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        """Stop heartbeat updates"""
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        """Periodic status update loop - every 2 seconds."""
        while self.is_running:
            try:
                elapsed = int(time.time() - self.start_time)

                # Get animated spinner
                spinner = self.SPINNERS[self._spinner_idx % len(self.SPINNERS)]
                self._spinner_idx += 1

                # Get emoji for current action
                emoji = self.ACTION_EMOJIS.get(self._current_action, "🤖")

                # Format time nicely
                if elapsed < 60:
                    time_str = f"{elapsed}s"
                else:
                    mins = elapsed // 60
                    secs = elapsed % 60
                    time_str = f"{mins}m {secs}s"

                # Get action label
                label = self.ACTION_LABELS.get(self._current_action, "Working")

                # Build status line with HTML formatting (stable, no flickering):
                # emoji <b>action</b> spinner (time) <i>detail</i>
                if self._action_detail:
                    status = f"{emoji} <b>{label}</b> {spinner} ({time_str}) · <i>{self._action_detail}</i>"
                else:
                    status = f"{emoji} <b>{label}...</b> {spinner} ({time_str})"

                # set_status() causes _do_update() -> coordinator (2with interval)
                await self.streaming.set_status(status)
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")
                break


@dataclass
class FileChange:
    """Represents a single file change."""
    file_path: str
    action: str  # "create", "edit", "delete"
    lines_added: int = 0
    lines_removed: int = 0


class FileChangeTracker:
    """
    Tracks file changes during a Claude session.

    Monitors Edit, Write, and Bash (for git operations) tool uses
    to build a summary of all modifications.

    Usage:
        tracker = FileChangeTracker()
        tracker.track_tool_use("Edit", {"file_path": "/app/main.py", ...})
        tracker.track_tool_result("Edit", "...edited 5 lines...")
        summary = tracker.get_summary()
    """

    def __init__(self):
        self._changes: dict[str, FileChange] = {}  # file_path -> FileChange
        self._current_tool: Optional[str] = None
        self._current_file: Optional[str] = None

    def track_tool_use(self, tool_name: str, tool_input: dict) -> None:
        """
        Track a tool use event.

        Args:
            tool_name: Name of the tool (Edit, Write, Bash, etc.)
            tool_input: Tool input parameters
        """
        tool_lower = tool_name.lower()
        self._current_tool = tool_lower

        if tool_lower == "write":
            file_path = tool_input.get("file_path", "")
            if file_path:
                self._current_file = file_path
                content = tool_input.get("content", "")
                lines = content.count('\n') + 1 if content else 0

                # Check if file exists (new or overwrite)
                if file_path in self._changes:
                    # Overwriting existing tracked file
                    self._changes[file_path].lines_added += lines
                    self._changes[file_path].action = "edit"
                else:
                    self._changes[file_path] = FileChange(
                        file_path=file_path,
                        action="create",
                        lines_added=lines,
                        lines_removed=0
                    )

        elif tool_lower == "edit":
            file_path = tool_input.get("file_path", "")
            if file_path:
                self._current_file = file_path
                old_string = tool_input.get("old_string", "")
                new_string = tool_input.get("new_string", "")

                old_lines = old_string.count('\n') + 1 if old_string else 0
                new_lines = new_string.count('\n') + 1 if new_string else 0

                if file_path in self._changes:
                    self._changes[file_path].lines_added += new_lines
                    self._changes[file_path].lines_removed += old_lines
                else:
                    self._changes[file_path] = FileChange(
                        file_path=file_path,
                        action="edit",
                        lines_added=new_lines,
                        lines_removed=old_lines
                    )

        elif tool_lower == "bash":
            command = tool_input.get("command", "")
            # Track git-related commands
            if "git add" in command or "git commit" in command:
                # Git operations are tracked but don't count as file changes
                pass
            elif "rm " in command or "del " in command:
                # Try to extract file path from delete commands
                import shlex
                try:
                    parts = shlex.split(command)
                    for i, part in enumerate(parts):
                        if part in ("rm", "del") and i + 1 < len(parts):
                            file_path = parts[i + 1]
                            if not file_path.startswith("-"):
                                self._changes[file_path] = FileChange(
                                    file_path=file_path,
                                    action="delete",
                                    lines_added=0,
                                    lines_removed=0
                                )
                except Exception:
                    pass

    def track_tool_result(self, tool_name: str, output: str) -> None:
        """
        Track a tool result to update change statistics.

        Args:
            tool_name: Name of the tool
            output: Tool output/result
        """
        # Could parse output for more accurate line counts
        # For now, we rely on input-based tracking
        self._current_tool = None
        self._current_file = None

    def get_changes(self) -> list[FileChange]:
        """Get list of all tracked changes."""
        return list(self._changes.values())

    def get_summary(self) -> str:
        """
        Generate a Cursor-style summary of file changes.

        Returns:
            Formatted summary string with file changes
        """
        if not self._changes:
            return ""

        lines = ["📊 <b>Changed files:</b>\n"]

        total_added = 0
        total_removed = 0

        # Sort by action: creates first, then edits, then deletes
        action_order = {"create": 0, "edit": 1, "delete": 2}
        sorted_changes = sorted(
            self._changes.values(),
            key=lambda c: (action_order.get(c.action, 1), c.file_path)
        )

        for change in sorted_changes:
            # Get just the filename for display
            filename = change.file_path.split("/")[-1].split("\\")[-1]

            # Action emoji
            if change.action == "create":
                action_emoji = "✨"
            elif change.action == "delete":
                action_emoji = "🗑️"
            else:
                action_emoji = "📝"

            # Format line changes
            changes_str = ""
            if change.lines_added > 0:
                changes_str += f"<code>+{change.lines_added}</code>"
                total_added += change.lines_added
            if change.lines_removed > 0:
                if changes_str:
                    changes_str += " "
                changes_str += f"<code>-{change.lines_removed}</code>"
                total_removed += change.lines_removed

            if changes_str:
                lines.append(f"  {action_emoji} <code>{filename}</code> {changes_str}")
            else:
                lines.append(f"  {action_emoji} <code>{filename}</code>")

        # Total summary
        if total_added > 0 or total_removed > 0:
            total_str = ""
            if total_added > 0:
                total_str += f"<code>+{total_added}</code>"
            if total_removed > 0:
                if total_str:
                    total_str += " "
                total_str += f"<code>-{total_removed}</code>"
            lines.append(f"\n<i>Total: {len(self._changes)} file(s)), {total_str}</i>")

        return "\n".join(lines)

    def has_changes(self) -> bool:
        """Check if any changes were tracked."""
        return len(self._changes) > 0

    def reset(self) -> None:
        """Clear all tracked changes."""
        self._changes.clear()
        self._current_tool = None
        self._current_file = None
