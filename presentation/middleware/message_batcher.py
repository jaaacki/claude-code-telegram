"""
Message Batcher Middleware

Combines multiple messages from one user,
arrived in a short period of time (0.5c), in one request.

This solves the problem when the user sends several messages in a row
and each of them runs a separate task Claude.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Awaitable, Optional, Any
from datetime import datetime

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)


@dataclass
class PendingBatch:
    """Waiting batch messages"""
    messages: List[Message] = field(default_factory=list)
    timer_task: Optional[asyncio.Task] = None
    created_at: datetime = field(default_factory=datetime.now)


class MessageBatcher:
    """
    Collects multiple messages from one user in batch.

    Logics:
    1. The first message starts the timer for BATCH_DELAY seconds
    2. Each new message is added to batch and resets the timer
    3. When the timer fires, all messages are combined and processed

    Special cases (NOT batched, processed immediately):
    - Teams (/start, /cancel etc.)
    - Documents and photos
    - Messages while waiting for input (HITL, variables)
    """

    BATCH_DELAY = 0.5  # seconds

    def __init__(self, batch_delay: float = 0.5):
        self.batch_delay = batch_delay
        self._batches: Dict[int, PendingBatch] = {}
        self._lock = asyncio.Lock()

    def is_batching(self, user_id: int) -> bool:
        """Check if active batch for the user"""
        return user_id in self._batches

    async def add_message(
        self,
        message: Message,
        process_callback: Callable[[Message, str], Awaitable[None]]
    ) -> bool:
        """
        Add a message to batch.

        Args:
            message: Telegram message
            process_callback: Function for processing merged messages
                             Signature: (original_message, combined_text) -> None

        Returns:
            True if the message is added to batch,
            False If batch processed immediately
        """
        user_id = message.from_user.id
        text = message.text or ""

        async with self._lock:
            if user_id not in self._batches:
                # First message - creating batch
                self._batches[user_id] = PendingBatch(messages=[message])
                logger.debug(f"[{user_id}] Created new batch with message: {text[:50]}...")
            else:
                # Add to existing batch
                batch = self._batches[user_id]
                batch.messages.append(message)

                # Cancel the old timer
                if batch.timer_task and not batch.timer_task.done():
                    batch.timer_task.cancel()
                    # Use timeout to prevent memory leak if task hangs
                    try:
                        await asyncio.wait_for(batch.timer_task, timeout=0.1)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        # Expected: task was cancelled or timed out
                        pass
                    except Exception as e:
                        # Unexpected error, but don't crash
                        logger.warning(f"[{user_id}] Error waiting for cancelled timer: {e}")

                logger.debug(f"[{user_id}] Added to batch ({len(batch.messages)} messages): {text[:50]}...")

            # Starting a new timer
            batch = self._batches[user_id]
            batch.timer_task = asyncio.create_task(
                self._process_after_delay(user_id, process_callback)
            )

        return True

    async def _process_after_delay(
        self,
        user_id: int,
        process_callback: Callable[[Message, str], Awaitable[None]]
    ):
        """Process batch after a delay"""
        try:
            await asyncio.sleep(self.batch_delay)

            async with self._lock:
                batch = self._batches.pop(user_id, None)

            if not batch or not batch.messages:
                return

            # We combine all texts
            texts = [m.text for m in batch.messages if m.text]
            combined_text = "\n\n".join(texts)

            # Using the first message as a basis
            first_message = batch.messages[0]

            msg_count = len(batch.messages)
            if msg_count > 1:
                logger.info(
                    f"[{user_id}] Batched {msg_count} messages into one request"
                )

            # Calling callback with merged text
            await process_callback(first_message, combined_text)

        except asyncio.CancelledError:
            # Timer canceled - new message arrived
            pass
        except Exception as e:
            logger.error(f"[{user_id}] Error processing batch: {e}", exc_info=True)
            # Trying to clean it up batch in case of error
            async with self._lock:
                self._batches.pop(user_id, None)

    async def cancel_batch(self, user_id: int) -> List[Message]:
        """
        Cancel batch and return accumulated messages.
        Used when messages need to be processed immediately.
        """
        async with self._lock:
            batch = self._batches.pop(user_id, None)

            if batch:
                if batch.timer_task and not batch.timer_task.done():
                    batch.timer_task.cancel()
                return batch.messages

            return []

    async def flush_batch(
        self,
        user_id: int,
        process_callback: Callable[[Message, str], Awaitable[None]]
    ) -> bool:
        """
        Force processing batch now (without waiting for timer).
        Returns True If batch was processed.
        """
        messages = await self.cancel_batch(user_id)

        if messages:
            texts = [m.text for m in messages if m.text]
            combined_text = "\n\n".join(texts)
            await process_callback(messages[0], combined_text)
            return True

        return False


class MessageBatcherMiddleware(BaseMiddleware):
    """
    Aiogram middleware For batching messages.

    Intercepts text messages and merges them.
    Passes through without changes:
    - Commands (beginning with /)
    - Documents and photos
    - Callback queries
    """

    def __init__(
        self,
        batcher: MessageBatcher,
        should_batch_callback: Optional[Callable[[Message], Awaitable[bool]]] = None
    ):
        """
        Args:
            batcher: Instance MessageBatcher
            should_batch_callback: Function for checking whether a message needs to be batched.
                                   If None - all text messages without commands are batched.
        """
        self.batcher = batcher
        self.should_batch_callback = should_batch_callback
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # We work only with messages
        if not isinstance(event, Message):
            return await handler(event, data)

        message: Message = event

        # Checking whether it is necessary to batch
        should_batch = await self._should_batch(message)

        if not should_batch:
            # We process immediately
            return await handler(event, data)

        # Add to batch
        async def process_batched(first_message: Message, combined_text: str):
            # We create data with merged text
            data['batched_text'] = combined_text
            data['is_batched'] = True
            data['batch_original_text'] = first_message.text
            await handler(first_message, data)

        await self.batcher.add_message(message, process_batched)

        # We return None - the message will be processed later
        return None

    async def _should_batch(self, message: Message) -> bool:
        """Determine whether the message needs to be batched"""
        # Don't batch if there is no text
        if not message.text:
            return False

        # We don’t batch teams
        if message.text.startswith('/'):
            return False

        # We do not batch documents and photos
        if message.document or message.photo:
            return False

        # If there is custom callback - use it
        if self.should_batch_callback:
            return await self.should_batch_callback(message)

        return True
