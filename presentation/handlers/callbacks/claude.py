"""
Claude Callback Handlers

Handles HITL (Human-in-the-Loop) callbacks:
- Permission approval/rejection
- Question answering
- Plan approval
- Task cancellation
"""

import logging
from aiogram.types import CallbackQuery

from presentation.handlers.callbacks.base import BaseCallbackHandler
from presentation.keyboards.keyboards import CallbackData
from shared.constants import TEXT_TRUNCATE_LIMIT

logger = logging.getLogger(__name__)


class ClaudeCallbackHandler(BaseCallbackHandler):
    """Handles Claude Code HITL callbacks."""

    async def _get_user_id_from_callback(self, callback: CallbackQuery) -> int:
        """Extract user_id from callback data."""
        data = CallbackData.parse_claude_callback(callback.data)
        return int(data.get("user_id", 0))

    async def _validate_user(self, callback: CallbackQuery) -> int | None:
        """Validate user and return user_id if valid."""
        user_id = await self._get_user_id_from_callback(callback)
        if user_id != callback.from_user.id:
            await callback.answer("❌ This action is not for you")
            return None
        return user_id

    async def _truncate_and_append(self, text: str, suffix: str) -> str:
        """Truncate text if needed and append suffix."""
        if len(text) > TEXT_TRUNCATE_LIMIT:
            text = text[:TEXT_TRUNCATE_LIMIT] + "\n... (truncated)"
        return text + suffix

    # ============== Permission Callbacks ==============

    async def handle_claude_approve(self, callback: CallbackQuery) -> None:
        """Handle Claude Code permission approval"""
        user_id = await self._validate_user(callback)
        if not user_id:
            return

        try:
            original_text = callback.message.text or ""
            await callback.message.edit_text(
                original_text + "\n\n✅ Approved",
                parse_mode=None
            )

            if self.claude_proxy:
                await self.claude_proxy.respond_to_permission(user_id, True)

            if hasattr(self.message_handlers, 'handle_permission_response'):
                await self.message_handlers.handle_permission_response(user_id, True)

            await callback.answer("✅ Approved")

        except Exception as e:
            logger.error(f"Error handling claude approve: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_claude_reject(self, callback: CallbackQuery) -> None:
        """Handle Claude Code permission rejection"""
        user_id = await self._validate_user(callback)
        if not user_id:
            return

        try:
            original_text = callback.message.text or ""
            await callback.message.edit_text(
                original_text + "\n\n❌ Rejected",
                parse_mode=None
            )

            if self.claude_proxy:
                await self.claude_proxy.respond_to_permission(user_id, False)

            if hasattr(self.message_handlers, 'handle_permission_response'):
                await self.message_handlers.handle_permission_response(user_id, False)

            await callback.answer("❌ Rejected")

        except Exception as e:
            logger.error(f"Error handling claude reject: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_claude_clarify(self, callback: CallbackQuery) -> None:
        """Handle Claude Code permission clarification request"""
        user_id = await self._validate_user(callback)
        if not user_id:
            return

        try:
            hitl = self.message_handlers._hitl if hasattr(self.message_handlers, '_hitl') else None
            if not hitl:
                await callback.answer("❌ HITL manager unavailable")
                return

            hitl.set_expecting_clarification(user_id, True)
            logger.info(f"[{user_id}] Set expecting_clarification=True for permission clarification")

            original_text = callback.message.text or ""
            await callback.message.edit_text(
                original_text + "\n\n💬 Enter specification:",
                parse_mode=None
            )

            await callback.answer("✏️ Enter clarification text")

        except Exception as e:
            logger.error(f"Error handling claude clarify: {e}")
            await callback.answer(f"❌ Error: {e}")

    # ============== Question Callbacks ==============

    async def handle_claude_answer(self, callback: CallbackQuery) -> None:
        """Handle Claude Code question answer (selected option)"""
        data = CallbackData.parse_claude_callback(callback.data)
        user_id = int(data.get("user_id", 0))
        option_index = int(data.get("option_index", 0))

        if user_id != callback.from_user.id:
            await callback.answer("❌ This action is not for you")
            return

        try:
            answer = str(option_index)
            if hasattr(self.message_handlers, 'get_pending_question_option'):
                answer = self.message_handlers.get_pending_question_option(user_id, option_index)

            original_text = callback.message.text or ""
            await callback.message.edit_text(
                original_text + f"\n\n📝 Answer: {answer}",
                parse_mode=None
            )

            if self.sdk_service:
                await self.sdk_service.respond_to_question(user_id, answer)
            elif self.claude_proxy:
                await self.claude_proxy.respond_to_question(user_id, answer)

            if hasattr(self.message_handlers, 'handle_question_response'):
                await self.message_handlers.handle_question_response(user_id, answer)

            await callback.answer(f"Answer: {answer[:20]}...")

        except Exception as e:
            logger.error(f"Error handling claude answer: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_claude_other(self, callback: CallbackQuery) -> None:
        """Handle Claude Code question - user wants to type custom answer"""
        user_id = await self._validate_user(callback)
        if not user_id:
            return

        try:
            original_text = callback.message.text or ""
            await callback.message.edit_text(
                original_text + "\n\n✏️ Type your answer below:",
                parse_mode=None
            )

            if hasattr(self.message_handlers, 'set_expecting_answer'):
                self.message_handlers.set_expecting_answer(user_id, True)

            await callback.answer("Enter your answer in the chat")

        except Exception as e:
            logger.error(f"Error handling claude other: {e}")
            await callback.answer(f"❌ Error: {e}")

    # ============== Task Control Callbacks ==============

    async def handle_claude_cancel(self, callback: CallbackQuery) -> None:
        """Handle Claude Code task cancellation"""
        user_id = await self._validate_user(callback)
        if not user_id:
            return

        try:
            cancelled = False

            if self.sdk_service:
                cancelled = await self.sdk_service.cancel_task(user_id)
                logger.info(f"SDK cancel_task for user {user_id}: {cancelled}")

            if not cancelled and self.claude_proxy:
                cancelled = await self.claude_proxy.cancel_task(user_id)
                logger.info(f"Proxy cancel_task for user {user_id}: {cancelled}")

            if cancelled:
                await callback.message.edit_text("🛑 Task canceled", parse_mode=None)
                await callback.answer("Task canceled")
            else:
                await callback.answer("No active task to cancel")

        except Exception as e:
            logger.error(f"Error cancelling task: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_claude_continue(self, callback: CallbackQuery) -> None:
        """Handle continue Claude Code session"""
        data = CallbackData.parse_claude_callback(callback.data)
        user_id = int(data.get("user_id", 0))
        session_id = data.get("session_id")

        if user_id != callback.from_user.id:
            await callback.answer("❌ This action is not for you")
            return

        try:
            await callback.message.edit_text(
                "▶️ Continue the session...\n\nSend the following message to continue.",
                parse_mode=None
            )

            if hasattr(self.message_handlers, 'set_continue_session'):
                self.message_handlers.set_continue_session(user_id, session_id)

            await callback.answer("Send the following message")

        except Exception as e:
            logger.error(f"Error continuing session: {e}")
            await callback.answer(f"❌ Error: {e}")

    # ============== Plan Approval Callbacks (ExitPlanMode) ==============

    async def _get_plan_user_id(self, callback: CallbackQuery) -> int:
        """Extract user_id from plan callback data."""
        parts = callback.data.split(":")
        return int(parts[2]) if len(parts) > 2 else 0

    async def handle_plan_approve(self, callback: CallbackQuery) -> None:
        """Handle plan approval - user approves the implementation plan"""
        user_id = self._get_plan_user_id(callback)

        if user_id != callback.from_user.id:
            await callback.answer("❌ This action is not for you")
            return

        try:
            original_text = callback.message.text or ""
            text = await self._truncate_and_append(
                original_text,
                "\n\n✅ **Plan approved** — I start execution!"
            )
            await callback.message.edit_text(text, parse_mode=None)

            if hasattr(self.message_handlers, 'handle_plan_response'):
                await self.message_handlers.handle_plan_response(user_id, "approve")

            await callback.answer("✅ Plan approved!")

        except Exception as e:
            logger.error(f"Error handling plan approve: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_plan_reject(self, callback: CallbackQuery) -> None:
        """Handle plan rejection - user rejects the plan"""
        user_id = self._get_plan_user_id(callback)

        if user_id != callback.from_user.id:
            await callback.answer("❌ This action is not for you")
            return

        try:
            original_text = callback.message.text or ""
            text = await self._truncate_and_append(original_text, "\n\n❌ **Plan rejected**")
            await callback.message.edit_text(text, parse_mode=None)

            if hasattr(self.message_handlers, 'handle_plan_response'):
                await self.message_handlers.handle_plan_response(user_id, "reject")

            await callback.answer("❌ Plan rejected")

        except Exception as e:
            logger.error(f"Error handling plan reject: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_plan_clarify(self, callback: CallbackQuery) -> None:
        """Handle plan clarification - user wants to provide feedback"""
        user_id = self._get_plan_user_id(callback)

        if user_id != callback.from_user.id:
            await callback.answer("❌ This action is not for you")
            return

        try:
            original_text = callback.message.text or ""
            text = await self._truncate_and_append(
                original_text,
                "\n\n✏️ **Clarification of the plan**\n\nEnter your comments into the chat:"
            )
            await callback.message.edit_text(text, parse_mode=None)

            if hasattr(self.message_handlers, 'set_expecting_plan_clarification'):
                self.message_handlers.set_expecting_plan_clarification(user_id, True)

            await callback.answer("Enter clarifications into the chat")

        except Exception as e:
            logger.error(f"Error handling plan clarify: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_plan_cancel(self, callback: CallbackQuery) -> None:
        """Handle plan cancellation - user wants to cancel the entire task"""
        user_id = self._get_plan_user_id(callback)

        if user_id != callback.from_user.id:
            await callback.answer("❌ This action is not for you")
            return

        try:
            await callback.message.edit_text("🛑 **Task canceled**", parse_mode=None)

            cancelled = False
            if self.sdk_service:
                cancelled = await self.sdk_service.cancel_task(user_id)

            if not cancelled and self.claude_proxy:
                cancelled = await self.claude_proxy.cancel_task(user_id)

            if hasattr(self.message_handlers, 'handle_plan_response'):
                await self.message_handlers.handle_plan_response(user_id, "cancel")

            await callback.answer("🛑 Task canceled")

        except Exception as e:
            logger.error(f"Error handling plan cancel: {e}")
            await callback.answer(f"❌ Error: {e}")
