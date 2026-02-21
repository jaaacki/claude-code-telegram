"""
Message Update Coordinator

Centralized point for ALL message updates Telegram.
Prevents rate limiting by:
1. Single queue of updates per message
2. Strict interval 2 seconds between updates
3. Combining multiple queries into one
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Callable, Awaitable, Any

from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

logger = logging.getLogger(__name__)


@dataclass
class PendingUpdate:
    """Pending message update."""
    text: str
    parse_mode: Optional[str] = "HTML"
    reply_markup: Optional[InlineKeyboardMarkup] = None
    priority: int = 0  # Higher = more important (final updates have the highest priority)
    is_final: bool = False  # Final update - ignore subsequent ones


@dataclass
class MessageState:
    """Single message status."""
    message: Message
    last_update_time: float = 0.0
    last_sent_text: str = ""
    pending_update: Optional[PendingUpdate] = None
    update_task: Optional[asyncio.Task] = None
    is_finalized: bool = False


class MessageUpdateCoordinator:
    """
    Message Update Coordinator Telegram.

    IMPORTANT: All message updates MUST go through this class!

    Guarantees:
    - Minimum 2 seconds between updates of one message
    - Multiple requests are merged (last one wins)
    - Rate limit processed gracefully
    - Final updates take priority

    Usage:
        coordinator = MessageUpdateCoordinator(bot)

        # Regular update (will be delayed if <2from from the past)
        await coordinator.update(message, "new text")

        # Final update (guaranteed to complete)
        await coordinator.update(message, "final", is_final=True)
    """

    # Strict minimum interval between updates
    MIN_UPDATE_INTERVAL = 2.0  # seconds

    # Maximum waiting time rate limit
    MAX_RATE_LIMIT_WAIT = 10.0  # seconds

    def __init__(self, bot: Bot):
        self.bot = bot
        self._messages: Dict[int, MessageState] = {}  # message_id -> state
        self._global_lock = asyncio.Lock()

    def _get_state(self, message: Message) -> MessageState:
        """Get or create message state."""
        msg_id = message.message_id
        if msg_id not in self._messages:
            self._messages[msg_id] = MessageState(message=message)
        return self._messages[msg_id]

    async def update(
        self,
        message: Message,
        text: str,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        is_final: bool = False,
        priority: int = 0
    ) -> bool:
        """
        Schedule message update.

        Args:
            message: Message for update
            text: New text
            parse_mode: Parsing mode (HTML, Markdown, None)
            reply_markup: Keyboard
            is_final: Final update (priority, guaranteed to complete)
            priority: Priority (0=ordinary, 1=important, 2=critical)

        Returns:
            True if an update is planned/completed
        """
        state = self._get_state(message)

        # Logging an incoming call
        logger.info(
            f"Coordinator.update: msg={message.message_id}, text={len(text)}ch, "
            f"is_final={is_final}, last_sent={len(state.last_sent_text)}ch"
        )

        # Ignore updates for finalized messages
        if state.is_finalized and not is_final:
            logger.debug(f"Message {message.message_id}: ignoring update, already finalized")
            return False

        # If the text has not changed, skip it
        if text == state.last_sent_text and not is_final:
            logger.debug(f"Message {message.message_id}: text unchanged ({len(text)}ch), skipping")
            return False

        # We create pending update
        pending = PendingUpdate(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            priority=priority if not is_final else 100,
            is_final=is_final
        )

        # If there is pending with lower priority - replace
        if state.pending_update is None or pending.priority >= state.pending_update.priority:
            state.pending_update = pending
            logger.debug(f"Message {message.message_id}: pending_update set ({len(text)}ch)")

        # Let's check if it's possible to update now
        now = time.time()
        time_since_update = now - state.last_update_time

        if time_since_update >= self.MIN_UPDATE_INTERVAL or is_final:
            # You can update now
            logger.info(f"Message {message.message_id}: executing update NOW (elapsed={time_since_update:.1f}s)")
            return await self._execute_update(state)
        else:
            # We are planning a delayed update
            delay = self.MIN_UPDATE_INTERVAL - time_since_update
            logger.info(f"Message {message.message_id}: scheduling update in {delay:.1f}s")
            await self._schedule_update(state, delay)
            return True

    async def _schedule_update(self, state: MessageState, delay: float) -> None:
        """Schedule a delayed update."""
        # If you already have scheduled task - he will do pending_update
        # pending_update has already been updated in update() before this call
        if state.update_task and not state.update_task.done():
            pending_size = len(state.pending_update.text) if state.pending_update else 0
            logger.debug(
                f"Message {state.message.message_id}: task already scheduled, "
                f"pending updated to {pending_size}ch (will be sent when task fires)"
            )
            return

        async def delayed_update():
            await asyncio.sleep(delay)
            logger.debug(f"Message {state.message.message_id}: delayed_update firing after {delay:.1f}s")
            await self._execute_update(state)

        state.update_task = asyncio.create_task(delayed_update())
        pending_size = len(state.pending_update.text) if state.pending_update else 0
        logger.info(f"Message {state.message.message_id}: NEW scheduled update in {delay:.1f}s ({pending_size}ch)")

    async def _execute_update(self, state: MessageState) -> bool:
        """Perform message update."""
        pending = state.pending_update
        if not pending:
            logger.debug(f"Message {state.message.message_id}: _execute_update - no pending update")
            return False

        # Cleaning pending before execution (so that new requests create a new)
        state.pending_update = None

        # Final update
        if pending.is_final:
            state.is_finalized = True

        # CRITICAL LOGING - the moment of sending to Telegram
        logger.info(
            f">>> TELEGRAM EDIT: msg={state.message.message_id}, "
            f"text={len(pending.text)}ch, is_final={pending.is_final}"
        )

        try:
            await state.message.edit_text(
                pending.text,
                parse_mode=pending.parse_mode,
                reply_markup=pending.reply_markup
            )
            state.last_update_time = time.time()
            state.last_sent_text = pending.text
            logger.info(f">>> TELEGRAM EDIT SUCCESS: msg={state.message.message_id}, {len(pending.text)}ch")
            return True

        except TelegramRetryAfter as e:
            # Rate limited
            if e.retry_after > self.MAX_RATE_LIMIT_WAIT:
                logger.warning(
                    f"Message {state.message.message_id}: rate limited for {e.retry_after}s, "
                    f"skipping (max wait {self.MAX_RATE_LIMIT_WAIT}s)"
                )
                # For the final ones, we'll try later
                if pending.is_final:
                    state.is_finalized = False
                    state.pending_update = pending
                    await self._schedule_update(state, self.MAX_RATE_LIMIT_WAIT)
                return False

            # Short rate limit - wait and repeat
            logger.info(f"Message {state.message.message_id}: rate limited, waiting {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 0.5)
            state.pending_update = pending  # We restore
            return await self._execute_update(state)

        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                # The content has not changed - this is normal
                state.last_update_time = time.time()
                state.last_sent_text = pending.text
                return True
            elif "message to edit not found" in str(e).lower():
                # Post deleted
                logger.warning(f"Message {state.message.message_id}: deleted, removing from coordinator")
                self._messages.pop(state.message.message_id, None)
                return False
            else:
                logger.error(f"Message {state.message.message_id}: Telegram error: {e}")
                # Trying without formatting
                try:
                    import re
                    plain_text = re.sub(r'<[^>]+>', '', pending.text)
                    await state.message.edit_text(
                        plain_text,
                        parse_mode=None,
                        reply_markup=pending.reply_markup
                    )
                    state.last_update_time = time.time()
                    state.last_sent_text = plain_text
                    return True
                except Exception:
                    return False

        except Exception as e:
            logger.error(f"Message {state.message.message_id}: unexpected error: {e}")
            return False

    async def send_new(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> Optional[Message]:
        """
        Send new message.

        Automatically registers it with the coordinator.
        """
        try:
            message = await self.bot.send_message(
                chat_id,
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            # Register with the coordinator
            state = self._get_state(message)
            state.last_update_time = time.time()
            state.last_sent_text = text
            return message

        except TelegramRetryAfter as e:
            if e.retry_after > self.MAX_RATE_LIMIT_WAIT:
                logger.error(f"send_new: rate limited for {e.retry_after}s, giving up")
                return None
            logger.info(f"send_new: rate limited, waiting {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 0.5)
            return await self.send_new(chat_id, text, parse_mode, reply_markup)

        except TelegramBadRequest as e:
            logger.error(f"send_new: Telegram error: {e}")
            # Trying without formatting
            try:
                import re
                plain_text = re.sub(r'<[^>]+>', '', text)
                message = await self.bot.send_message(
                    chat_id,
                    plain_text,
                    parse_mode=None,
                    reply_markup=reply_markup
                )
                state = self._get_state(message)
                state.last_update_time = time.time()
                state.last_sent_text = plain_text
                return message
            except Exception:
                return None

        except Exception as e:
            logger.error(f"send_new: unexpected error: {e}")
            return None

    def get_time_until_next_update(self, message: Message) -> float:
        """
        Get time until next possible update.

        Returns:
            Seconds until next update (0 if possible now)
        """
        state = self._get_state(message)
        elapsed = time.time() - state.last_update_time
        remaining = max(0, self.MIN_UPDATE_INTERVAL - elapsed)
        return remaining

    def is_finalized(self, message: Message) -> bool:
        """Check if the message is finalized."""
        state = self._get_state(message)
        return state.is_finalized

    def cleanup(self, message: Message) -> None:
        """Clear message status."""
        msg_id = message.message_id
        state = self._messages.pop(msg_id, None)
        if state and state.update_task:
            state.update_task.cancel()
        logger.debug(f"Message {msg_id}: cleaned up")

    def cleanup_chat(self, chat_id: int) -> None:
        """Clear all chat messages."""
        to_remove = [
            msg_id for msg_id, state in self._messages.items()
            if state.message.chat.id == chat_id
        ]
        for msg_id in to_remove:
            state = self._messages.pop(msg_id, None)
            if state and state.update_task:
                state.update_task.cancel()
        logger.debug(f"Chat {chat_id}: cleaned up {len(to_remove)} messages")


# Global coordinator instance (initialized in main.py)
_coordinator: Optional[MessageUpdateCoordinator] = None


def get_coordinator() -> Optional[MessageUpdateCoordinator]:
    """Get a global coordinator."""
    return _coordinator


def init_coordinator(bot: Bot) -> MessageUpdateCoordinator:
    """Initialize global coordinator."""
    global _coordinator
    _coordinator = MessageUpdateCoordinator(bot)
    logger.info("MessageUpdateCoordinator initialized")
    return _coordinator
