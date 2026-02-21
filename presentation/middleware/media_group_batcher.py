"""
Media Group Batcher

Collects all messages from one media group (album) before processing.

Telegram sends each file from the album as a separate message,
but they all have the same media_group_id. This butcher:
1. Collects all messages with the same media_group_id
2. Waiting for a short timeout (0.5c) to get all files
3. Calls callback with a list of all group messages
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Awaitable, Optional
from datetime import datetime

from aiogram.types import Message

logger = logging.getLogger(__name__)


@dataclass
class PendingMediaGroup:
    """Waiting media group"""
    media_group_id: str
    user_id: int
    messages: List[Message] = field(default_factory=list)
    timer_task: Optional[asyncio.Task] = None
    created_at: datetime = field(default_factory=datetime.now)


class MediaGroupBatcher:
    """
    Collects messages from one media group (album).

    Logics:
    1. First message from media_group_id starts the timer
    2. All subsequent messages with the same media_group_id are added to the group
    3. When the timer fires, it is called callback with all messages

    Usage:
        batcher = MediaGroupBatcher()

        async def process_album(messages: List[Message]):
            # Process all album files
            pass

        # IN handler:
        if message.media_group_id:
            await batcher.add_message(message, process_album)
            return  # Processing will happen later
    """

    BATCH_DELAY = 0.5  # seconds - waiting time for all album files

    def __init__(self, batch_delay: float = 0.5):
        self.batch_delay = batch_delay
        self._groups: Dict[str, PendingMediaGroup] = {}  # media_group_id -> group
        self._lock = asyncio.Lock()

    def is_collecting(self, media_group_id: str) -> bool:
        """Check if the group is meeting"""
        return media_group_id in self._groups

    async def add_message(
        self,
        message: Message,
        process_callback: Callable[[List[Message]], Awaitable[None]]
    ) -> bool:
        """
        Add a message to a media group.

        Args:
            message: Message from media_group_id
            process_callback: Function for processing collected messages
                             Signature: (messages: List[Message]) -> None

        Returns:
            True if the message is added to batch
        """
        media_group_id = message.media_group_id
        if not media_group_id:
            return False

        user_id = message.from_user.id

        async with self._lock:
            if media_group_id not in self._groups:
                # First message of the group - creating batch
                self._groups[media_group_id] = PendingMediaGroup(
                    media_group_id=media_group_id,
                    user_id=user_id,
                    messages=[message]
                )
                logger.info(
                    f"[{user_id}] Media group {media_group_id[:8]}... started, "
                    f"first file: {self._get_file_info(message)}"
                )
            else:
                # Add to an existing group
                group = self._groups[media_group_id]
                group.messages.append(message)

                # Cancel the old timer
                if group.timer_task and not group.timer_task.done():
                    group.timer_task.cancel()
                    try:
                        await asyncio.wait_for(group.timer_task, timeout=0.1)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass

                logger.debug(
                    f"[{user_id}] Media group {media_group_id[:8]}... "
                    f"added file #{len(group.messages)}: {self._get_file_info(message)}"
                )

            # Let's launch/restart the timer
            group = self._groups[media_group_id]
            group.timer_task = asyncio.create_task(
                self._process_after_delay(media_group_id, process_callback)
            )

        return True

    async def _process_after_delay(
        self,
        media_group_id: str,
        process_callback: Callable[[List[Message]], Awaitable[None]]
    ):
        """Process group after delay"""
        try:
            await asyncio.sleep(self.batch_delay)

            async with self._lock:
                group = self._groups.pop(media_group_id, None)

            if not group or not group.messages:
                return

            # Sort by message_id for the correct order
            group.messages.sort(key=lambda m: m.message_id)

            logger.info(
                f"[{group.user_id}] Media group {media_group_id[:8]}... complete: "
                f"{len(group.messages)} files"
            )

            # Calling callback
            await process_callback(group.messages)

        except asyncio.CancelledError:
            # Timer canceled - new message arrived
            pass
        except Exception as e:
            logger.error(f"Error processing media group {media_group_id}: {e}", exc_info=True)
            # Clearing a group in case of an error
            async with self._lock:
                self._groups.pop(media_group_id, None)

    def _get_file_info(self, message: Message) -> str:
        """Get file information for logging"""
        if message.photo:
            photo = message.photo[-1]
            return f"photo ({photo.file_size or 0} bytes)"
        elif message.document:
            doc = message.document
            return f"{doc.file_name or 'document'} ({doc.file_size or 0} bytes)"
        else:
            return "unknown"

    async def cancel_group(self, media_group_id: str) -> List[Message]:
        """
        Cancel group and return accumulated messages.
        """
        async with self._lock:
            group = self._groups.pop(media_group_id, None)

            if group:
                if group.timer_task and not group.timer_task.done():
                    group.timer_task.cancel()
                return group.messages

            return []

    def get_group_size(self, media_group_id: str) -> int:
        """Get current group size"""
        group = self._groups.get(media_group_id)
        return len(group.messages) if group else 0


# Global instance (created in main.py or container)
_media_group_batcher: Optional[MediaGroupBatcher] = None


def get_media_group_batcher() -> Optional[MediaGroupBatcher]:
    """Get Global batcher"""
    return _media_group_batcher


def init_media_group_batcher(batch_delay: float = 0.5) -> MediaGroupBatcher:
    """Initialize global batcher"""
    global _media_group_batcher
    _media_group_batcher = MediaGroupBatcher(batch_delay=batch_delay)
    logger.info(f"MediaGroupBatcher initialized (delay={batch_delay}s)")
    return _media_group_batcher
