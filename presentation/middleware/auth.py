import logging
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Awaitable, Any, Dict
from application.services.bot_service import BotService

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Middleware for user authorization on Message events"""

    def __init__(self, bot_service: BotService):
        super().__init__()
        self.bot_service = bot_service

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        """Check if user is authorized"""
        # Get user_id from the message
        if not event.from_user:
            logger.warning("Message has no from_user")
            return

        # Ignore messages from bots (including self)
        if event.from_user.is_bot:
            logger.debug(f"Ignoring message from bot: {event.from_user.id}")
            return

        user_id = event.from_user.id
        user_name = event.from_user.username or ""
        first_name = event.from_user.first_name or ""
        last_name = event.from_user.last_name or ""

        # Check whitelist first
        if not self.bot_service.is_user_allowed(user_id):
            logger.warning(f"Unauthorized access attempt from user_id: {user_id} (not in whitelist)")
            await event.answer("❌ You are not authorized to use this bot.")
            return

        # Get or create user (creates if not exists and whitelisted)
        user = await self.bot_service.get_or_create_user(user_id, user_name, first_name, last_name)
        if not user:
            logger.error(f"Failed to create user {user_id}")
            await event.answer("❌ Error creating user account.")
            return

        # Add user to data
        data["user"] = user
        return await handler(event, data)


class CallbackAuthMiddleware(BaseMiddleware):
    """Middleware for callback query authorization"""

    def __init__(self, bot_service: BotService):
        super().__init__()
        self.bot_service = bot_service

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        """Check if user is authorized for callback"""
        if not event.from_user:
            logger.warning("Callback query has no from_user")
            return

        user_id = event.from_user.id

        user = await self.bot_service.authorize_user(user_id)
        if not user:
            await event.answer("❌ You are not authorized.")
            return

        data["user"] = user
        return await handler(event, data)
