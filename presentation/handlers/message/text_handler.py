"""Text message handler"""

import logging
from typing import TYPE_CHECKING, Optional

from aiogram.types import Message
from aiogram import Bot

from presentation.keyboards.keyboards import Keyboards
from presentation.handlers.streaming import StreamingHandler, HeartbeatTracker
from domain.entities.claude_code_session import ClaudeCodeSession
from infrastructure.claude_code.proxy_service import TaskResult

from .base import BaseMessageHandler

if TYPE_CHECKING:
    from application.services.bot_service import BotService
    from application.services.project_service import ProjectService
    from application.services.context_service import ContextService
    from application.services.file_processor_service import ProcessedFile
    from presentation.handlers.state.user_state import UserStateManager
    from presentation.handlers.state.hitl_manager import HITLManager
    from presentation.handlers.state.variable_manager import VariableInputManager
    from presentation.handlers.state.plan_manager import PlanApprovalManager
    from presentation.handlers.state.file_context import FileContextManager

logger = logging.getLogger(__name__)


class TextMessageHandler(BaseMessageHandler):
    """Handles text message processing"""

    def __init__(
        self,
        bot_service: "BotService",
        user_state: "UserStateManager",
        hitl_manager: "HITLManager",
        file_context_manager: "FileContextManager",
        variable_manager: "VariableInputManager",
        plan_manager: "PlanApprovalManager",
        ai_request_handler=None,  # AIRequestHandler
        callback_handlers=None,
        project_service=None,
        context_service=None,
        file_processor_service=None,
        message_batcher=None,
        use_sdk: bool = True,
        sdk_service=None,
        claude_proxy=None,
        file_handler=None,
    ):
        super().__init__(
            bot_service=bot_service,
            user_state=user_state,
            hitl_manager=hitl_manager,
            file_context_manager=file_context_manager,
            variable_manager=variable_manager,
            plan_manager=plan_manager,
        )
        self.ai_request_handler = ai_request_handler
        self.callback_handlers = callback_handlers
        self.project_service = project_service
        self.context_service = context_service
        self.file_processor_service = file_processor_service
        self.message_batcher = message_batcher
        self.use_sdk = use_sdk
        self.sdk_service = sdk_service
        self.claude_proxy = claude_proxy
        self.file_handler = file_handler

    # Copied from legacy messages.py:557-870
    async def handle_text(
        self,
        message: Message,
        prompt_override: str = None,
        force_new_session: bool = False,
        _from_batcher: bool = False,
        **kwargs
    ) -> None:
        """Handle text messages - main entry point"""
        user_id = message.from_user.id
        bot = message.bot

        user = await self.bot_service.authorize_user(user_id)
        if not user:
            await message.answer("You are not authorized to use this bot.")
            return

        # Load yolo_mode from DB if not already loaded
        session = self.user_state.get(user_id)
        if session is None:
            # First interaction - load persisted settings
            await self.user_state.load_yolo_mode(user_id)

        # === FILE REPLY HANDLING ===
        reply = message.reply_to_message

        # Skip processing if this message already has enriched prompt (prevents infinite loop)
        if kwargs.get('_file_processed'):
            logger.debug(f"[{user_id}] File already processed, skipping file reply handling")
            # Continue to normal text processing with the enriched prompt
            pass
        elif reply and self.file_context_manager.has_files(reply.message_id) and self.file_processor_service:
            # Support for both single files and media groups (albums)
            cached_files = self.file_context_manager.pop_files(reply.message_id)
            if cached_files:
                # Get working directory for saving images (from project)
                working_dir = await self._get_project_working_dir(user_id)

                # Use appropriate method based on number of files
                if len(cached_files) == 1:
                    enriched_prompt = self.file_processor_service.format_for_prompt(
                        cached_files[0], message.text, working_dir=working_dir
                    )
                    files_info = cached_files[0].filename
                else:
                    enriched_prompt = self.file_processor_service.format_multiple_files_for_prompt(
                        cached_files, message.text, working_dir=working_dir
                    )
                    files_info = self.file_processor_service.get_files_summary(cached_files)

                task_preview = message.text[:50] + "..." if len(message.text) > 50 else message.text
                await message.answer(f"📎 Files: {files_info}\n📝 Task: {task_preview}\n\n⏳ I'm launching Claude Code...")
                # Execute task with file context and return (mark as processed to prevent re-processing)
                await self.handle_text(message, prompt_override=enriched_prompt, _file_processed=True)
                return

        elif reply and (reply.document or reply.photo) and self.file_processor_service:
            file_context = await self._extract_reply_file_context(reply, bot)
            if file_context:
                processed_file, _ = file_context
                # Get working directory for saving images (from project)
                working_dir = await self._get_project_working_dir(user_id)
                enriched_prompt = self.file_processor_service.format_for_prompt(
                    processed_file, message.text, working_dir=working_dir
                )
                task_preview = message.text[:50] + "..." if len(message.text) > 50 else message.text
                await message.answer(f"📎 File: {processed_file.filename}\n📝 Task: {task_preview}\n\n⏳ I'm launching Claude Code...")
                # Execute task with file context and return (mark as processed to prevent re-processing)
                await self.handle_text(message, prompt_override=enriched_prompt, _file_processed=True)
                return

        # === SPECIAL INPUT MODES (no batching - processed immediately) ===
        logger.debug(f"[{user_id}] Checking special input modes: "
                    f"expecting_answer={self.hitl_manager.is_expecting_answer(user_id)}, "
                    f"expecting_clarification={self.hitl_manager.is_expecting_clarification(user_id)}")

        if self.hitl_manager.is_expecting_answer(user_id):
            logger.info(f"[{user_id}] Handling answer input")
            await self._handle_answer_input(message)
            return

        if self.hitl_manager.is_expecting_clarification(user_id):
            logger.info(f"[{user_id}] Handling clarification input: {message.text[:50]}")
            await self._handle_clarification_input(message)
            return

        if self.hitl_manager.is_expecting_path(user_id):
            await self._handle_path_input(message)
            return

        if self.variable_manager.is_expecting_name(user_id):
            await self._handle_var_name_input(message)
            return

        if self.variable_manager.is_expecting_value(user_id):
            await self._handle_var_value_input(message)
            return

        if self.variable_manager.is_expecting_description(user_id):
            await self._handle_var_desc_input(message)
            return

        if self.plan_manager.is_expecting_clarification(user_id):
            await self._handle_plan_clarification(message)
            return

        # Check for global variable input (handled by CallbackHandlers)
        if hasattr(self, 'callback_handlers') and self.callback_handlers:
            if self.callback_handlers.is_gvar_input_active(user_id):
                handled = await self.callback_handlers.process_gvar_input(
                    user_id, message.text, message
                )
                if handled:
                    return

            # Check for other callback handler states (e.g., folder creation)
            if self.callback_handlers.get_user_state(user_id):
                handled = await self.callback_handlers.process_user_input(message)
                if handled:
                    return

        # === MESSAGE BATCHING ===
        # Combining multiple messages 0.5with in one request
        # DO NOT batch if: already from the butcher, there is prompt_override, or the task is already running
        if not _from_batcher and not prompt_override and not self.ai_request_handler._is_task_running(user_id):
            # Add a message to batch
            async def process_batched(first_msg: Message, combined_text: str):
                await self.handle_text(
                    first_msg,
                    prompt_override=combined_text,
                    force_new_session=force_new_session,
                    _from_batcher=True
                )

            await self.message_batcher.add_message(message, process_batched)
            return

        # === CHECK IF TASK RUNNING ===
        if self.ai_request_handler._is_task_running(user_id):
            await message.answer(
                "The task is already running.\n\n"
                "Use the cancel button or /cancel to stop.",
                reply_markup=Keyboards.claude_cancel(user_id)
            )
            return

        # === GET CONTEXT ===
        working_dir = self.user_state.get_working_dir(user_id)
        session_id = None if force_new_session else self.user_state.get_continue_session_id(user_id)
        context_id = None
        enriched_prompt = prompt_override if prompt_override else message.text

        if self.project_service and self.context_service:
            try:
                from domain.value_objects.user_id import UserId
                uid = UserId.from_int(user_id)

                project = await self.project_service.get_current(uid)
                if project:
                    working_dir = project.working_dir
                    context = await self.context_service.get_current(project.id)
                    if not context:
                        context = await self.context_service.create_new(
                            project.id, uid, "main", set_as_current=True
                        )

                    context_id = context.id

                    if not force_new_session and not session_id and context.claude_session_id:
                        session_id = context.claude_session_id
                        logger.info(
                            f"[{user_id}] Auto-continue: loaded session {session_id[:16]}... "
                            f"from context '{context.name}' (messages: {context.message_count})"
                        )

                    original_prompt = prompt_override if prompt_override else message.text
                    new_prompt = await self.context_service.get_enriched_prompt(
                        context_id, original_prompt, user_id=uid  # Pass user_id for global variables
                    )
                    if new_prompt != original_prompt:
                        enriched_prompt = new_prompt

                    self.user_state.set_working_dir(user_id, working_dir)

            except Exception as e:
                logger.warning(f"Error getting project/context: {e}")

        # === CREATE SESSION ===
        session = ClaudeCodeSession(
            user_id=user_id,
            working_dir=working_dir,
            claude_session_id=session_id
        )
        session.start_task(enriched_prompt)
        self.user_state.set_claude_session(user_id, session)

        if context_id:
            session.context_id = context_id
        session._original_working_dir = working_dir

        # === START STREAMING ===
        cancel_keyboard = Keyboards.claude_cancel(user_id)
        streaming = StreamingHandler(bot, message.chat.id, reply_markup=cancel_keyboard)

        yolo_indicator = " ⚡" if self.user_state.is_yolo_mode(user_id) else ""
        header = ""
        if self.project_service:
            try:
                from domain.value_objects.user_id import UserId
                uid = UserId.from_int(user_id)
                project = await self.project_service.get_current(uid)
                if project:
                    header = f"**{project.name}**{yolo_indicator}\n`{working_dir}`\n"
                else:
                    header = f"`{working_dir}`{yolo_indicator}\n"
            except Exception:
                header = f"`{working_dir}`{yolo_indicator}\n"
        else:
            header = f"`{working_dir}`{yolo_indicator}\n"

        await streaming.start(header)
        self.user_state.set_streaming_handler(user_id, streaming)

        # === SETUP HITL ===
        self.hitl_manager.create_permission_event(user_id)
        self.hitl_manager.create_question_event(user_id)

        heartbeat = HeartbeatTracker(streaming, interval=2.0)  # 2 seconds - coordinator provides rate limiting
        self.user_state.set_heartbeat(user_id, heartbeat)
        await heartbeat.start()

        try:
            if self.use_sdk and self.sdk_service:
                result = await self.sdk_service.run_task(
                    user_id=user_id,
                    prompt=enriched_prompt,
                    working_dir=working_dir,
                    session_id=session_id,
                    on_text=lambda text: self.ai_request_handler._on_text(user_id, text),
                    on_tool_use=lambda tool, inp: self.ai_request_handler._on_tool_use(user_id, tool, inp, message),
                    on_tool_result=lambda tid, out: self.ai_request_handler._on_tool_result(user_id, tid, out),
                    on_permission_request=lambda tool, details, inp: self.ai_request_handler._on_permission_sdk(
                        user_id, tool, details, inp, message
                    ),
                    on_permission_completed=lambda approved: self.ai_request_handler._on_permission_completed(user_id, approved),
                    on_question=lambda q, opts: self.ai_request_handler._on_question_sdk(user_id, q, opts, message),
                    on_question_completed=lambda answer: self.ai_request_handler._on_question_completed(user_id, answer),
                    on_plan_request=lambda plan_file, inp: self.ai_request_handler._on_plan_request(user_id, plan_file, inp, message),
                    on_thinking=lambda think: self.ai_request_handler._on_thinking(user_id, think),
                    on_error=lambda err: self.ai_request_handler._on_error(user_id, err),
                )

                if result.total_cost_usd and not result.cancelled:
                    streaming = self.user_state.get_streaming_handler(user_id)
                    if streaming:
                        cost_str = f"${result.total_cost_usd:.4f}"
                        # Build completion info from real usage data
                        info_parts = [cost_str]

                        # Add real token usage if available
                        if result.usage:
                            # Full context = direct input + cached reads + cache creation
                            input_tokens = result.usage.get("input_tokens", 0)
                            cache_read = result.usage.get("cache_read_input_tokens", 0)
                            cache_create = result.usage.get("cache_creation_input_tokens", 0)
                            output_tokens = result.usage.get("output_tokens", 0)

                            # Total context used (input side)
                            total_input = input_tokens + cache_read + cache_create
                            total_all = total_input + output_tokens

                            if total_all > 0:
                                # Show as "199K ctx | 1.2K out" for clarity
                                ctx_k = total_input / 1000
                                out_k = output_tokens / 1000
                                if ctx_k >= 1:
                                    info_parts.append(f"{ctx_k:.0f}K ctx")
                                if out_k >= 1:
                                    info_parts.append(f"{out_k:.1f}K out")
                                elif output_tokens > 0:
                                    info_parts.append(f"{output_tokens} out")

                        # Add duration if available
                        if result.duration_ms:
                            secs = result.duration_ms / 1000
                            if secs >= 60:
                                mins = int(secs // 60)
                                secs_rem = int(secs % 60)
                                info_parts.append(f"{mins}m{secs_rem}s")
                            else:
                                info_parts.append(f"{secs:.1f}s")

                        # Add turns
                        if result.num_turns:
                            info_parts.append(f"{result.num_turns} turns")

                        streaming.set_completion_info(" | ".join(info_parts))

                cli_result = TaskResult(
                    success=result.success,
                    output=result.output,
                    session_id=result.session_id,
                    error=result.error,
                    cancelled=result.cancelled,
                )
                await self.ai_request_handler._handle_result(user_id, cli_result, message)
            else:
                result = await self.claude_proxy.run_task(
                    user_id=user_id,
                    prompt=enriched_prompt,
                    working_dir=working_dir,
                    session_id=session_id,
                    on_text=lambda text: self.ai_request_handler._on_text(user_id, text),
                    on_tool_use=lambda tool, inp: self.ai_request_handler._on_tool_use(user_id, tool, inp, message),
                    on_tool_result=lambda tid, out: self.ai_request_handler._on_tool_result(user_id, tid, out),
                    on_permission=lambda tool, details: self.ai_request_handler._on_permission(user_id, tool, details, message),
                    on_question=lambda q, opts: self.ai_request_handler._on_question(user_id, q, opts, message),
                    on_error=lambda err: self.ai_request_handler._on_error(user_id, err),
                )
                await self.ai_request_handler._handle_result(user_id, result, message)

        except Exception as e:
            logger.error(f"Error running Claude Code: {e}")
            await streaming.send_error(str(e))
            session.fail(str(e))

        finally:
            await heartbeat.stop()
            self.hitl_manager.cleanup(user_id)
            self.user_state.remove_streaming_handler(user_id)
            self.user_state.remove_heartbeat(user_id)
            self.ai_request_handler._cleanup_step_handler(user_id)

    # === Helper Methods ===

    async def _get_project_working_dir(self, user_id: int) -> str:
        """Get working directory from current project (async, more accurate)"""
        if self.project_service:
            try:
                from domain.value_objects.user_id import UserId
                uid = UserId.from_int(user_id)
                project = await self.project_service.get_current(uid)
                if project and project.working_dir:
                    return project.working_dir
            except Exception as e:
                logger.warning(f"Error getting project working_dir: {e}")
        # Fallback to state
        return self.user_state.get_working_dir(user_id)

    async def _extract_reply_file_context(
        self, reply_message: Message, bot: Bot
    ) -> Optional[tuple["ProcessedFile", str]]:
        """Extract file from reply message - delegate to file_handler"""
        if self.file_handler:
            return await self.file_handler._extract_reply_file_context(reply_message, bot)
        return None

    # === Input Handlers (to be copied) ===

    # Copied from legacy messages.py:1393-1401
    async def _handle_answer_input(self, message: Message):
        """Handle text input for question answer"""
        user_id = message.from_user.id
        self.hitl_manager.set_expecting_answer(user_id, False)

        answer = message.text
        await message.answer(f"Answer: {answer[:50]}...")

        # Delegate to hitl_handler which is accessible through parent
        # For now, use the manager directly
        from presentation.handlers.message import HITLHandler
        # We need to handle this through the callback system
        # This will be wired up properly in coordinator
        logger.info(f"[{user_id}] Answer input: {answer[:50]}")

    # Copied from legacy messages.py:1403-1428
    async def _handle_clarification_input(self, message: Message):
        """Handle text input for permission clarification"""
        user_id = message.from_user.id
        logger.info(f"[{user_id}] _handle_clarification_input called with: {message.text[:100]}")

        self.hitl_manager.set_expecting_clarification(user_id, False)

        clarification = message.text.strip()
        preview = clarification[:50] + "..." if len(clarification) > 50 else clarification

        # Send clarification through permission response with approved=False
        logger.info(f"[{user_id}] Calling handle_permission_response with clarification")
        # This will be handled through the coordinator/callback system
        success = False  # Will be implemented in coordinator
        logger.info(f"[{user_id}] handle_permission_response returned: {success}")

        if success:
            await message.answer(f"💬 Clarification sent: {preview}")
        else:
            # No active permission request - clarification was ignored
            logger.warning(f"[{user_id}] Clarification was not accepted - no active permission request")
            await message.answer(
                f"⚠️ No active confirmation request.\n\n"
                f"Your clarification: {preview}\n\n"
                f"Submit this as a new request or wait for a request from Claude.",
                parse_mode=None
            )

    # Copied from legacy messages.py:1430-1448
    async def _handle_plan_clarification(self, message: Message):
        """Handle text input for plan clarification"""
        user_id = message.from_user.id
        self.plan_manager.set_expecting_clarification(user_id, False)

        clarification = message.text.strip()
        preview = clarification[:50] + "..." if len(clarification) > 50 else clarification

        # This will be handled through the coordinator/callback system
        success = False  # Will be implemented in coordinator

        if success:
            await message.answer(f"💬 Plan clarification sent: {preview}")
        else:
            await message.answer(
                f"⚠️ No active plan approval request.\n\n"
                f"Your clarification: {preview}\n\n"
                f"Submit this as a new request.",
                parse_mode=None
            )

    # Copied from legacy messages.py:1450-1458
    async def _handle_path_input(self, message: Message):
        """Handle text input for path"""
        user_id = message.from_user.id
        self.hitl_manager.set_expecting_path(user_id, False)

        path = message.text.strip()
        self.user_state.set_working_dir(user_id, path)

        await message.answer(f"Working folder set:\n{path}", parse_mode=None)

    # Copied from legacy messages.py:1460-1482
    async def _handle_var_name_input(self, message: Message):
        """Handle variable name input during add flow"""
        user_id = message.from_user.id
        var_name = message.text.strip().upper()

        result = self.variable_manager.validate_name(var_name)
        if not result.is_valid:
            await message.answer(
                f"Invalid variable name\n\n{result.error}",
                parse_mode=None,
                reply_markup=Keyboards.variable_cancel()
            )
            return

        menu_msg = self.variable_manager.get_menu_message(user_id)
        self.variable_manager.move_to_value_step(user_id, result.normalized_name)

        await message.answer(
            f"Enter a value for {result.normalized_name}:\n\n"
            f"For example: glpat-xxxx or Python/FastAPI",
            parse_mode=None,
            reply_markup=Keyboards.variable_cancel()
        )

    # Copied from legacy messages.py:1484-1530
    async def _handle_var_value_input(self, message: Message):
        """Handle variable value input during add/edit flow"""
        user_id = message.from_user.id
        var_name = self.variable_manager.get_var_name(user_id)
        var_value = message.text.strip()

        if not var_name:
            self.variable_manager.cancel(user_id)
            return

        result = self.variable_manager.validate_value(var_value)
        if not result.is_valid:
            await message.answer(result.error, reply_markup=Keyboards.variable_cancel())
            return

        is_editing = self.variable_manager.is_editing(user_id)

        if is_editing:
            old_desc = ""
            try:
                if self.context_service and self.project_service:
                    from domain.value_objects.user_id import UserId
                    uid = UserId.from_int(user_id)
                    project = await self.project_service.get_current(uid)
                    if project:
                        context = await self.context_service.get_current(project.id)
                        if context:
                            old_var = await self.context_service.get_variable(context.id, var_name)
                            if old_var:
                                old_desc = old_var.description
            except Exception:
                pass

            await self._save_variable(message, var_name, var_value, old_desc)
            return

        menu_msg = self.variable_manager.get_menu_message(user_id)
        self.variable_manager.move_to_description_step(user_id, var_value)

        await message.answer(
            f"Enter a description for {var_name}:\n\n"
            f"Describe what this variable does and how to use it.\n"
            f"For example: Token GitLab For git push/pull\n\n"
            f"Or click the button to skip.",
            parse_mode=None,
            reply_markup=Keyboards.variable_skip_description()
        )

    # Copied from legacy messages.py:1532-1542
    async def _handle_var_desc_input(self, message: Message):
        """Handle variable description input and save the variable"""
        user_id = message.from_user.id
        var_name, var_value = self.variable_manager.get_var_data(user_id)

        if not var_name or not var_value:
            self.variable_manager.cancel(user_id)
            return

        var_desc = message.text.strip()
        await self._save_variable(message, var_name, var_value, var_desc)

    # Copied from legacy messages.py:1554-1603
    async def _save_variable(self, message: Message, var_name: str, var_value: str, var_desc: str):
        """Save variable to context and show updated menu"""
        user_id = message.from_user.id

        if not self.project_service or not self.context_service:
            await message.answer("Services are not initialized")
            self.variable_manager.cancel(user_id)
            return

        try:
            from domain.value_objects.user_id import UserId
            uid = UserId.from_int(user_id)

            project = await self.project_service.get_current(uid)
            if not project:
                await message.answer("No active project. Use /change")
                self.variable_manager.cancel(user_id)
                return

            context = await self.context_service.get_current(project.id)
            if not context:
                await message.answer("No active context")
                self.variable_manager.cancel(user_id)
                return

            await self.context_service.set_variable(context.id, var_name, var_value, var_desc)

            self.variable_manager.complete(user_id)

            variables = await self.context_service.get_variables(context.id)

            display_val = var_value[:20] + "..." if len(var_value) > 20 else var_value
            desc_info = f"\n{var_desc}" if var_desc else ""

            await message.answer(
                f"Variable created\n\n"
                f"{var_name} = {display_val}"
                f"{desc_info}\n\n"
                f"Total variables: {len(variables)}",
                parse_mode=None,
                reply_markup=Keyboards.variables_menu(
                    variables, project.name, context.name,
                    show_back=True, back_to="menu:context"
                )
            )

        except Exception as e:
            logger.error(f"Error saving variable: {e}")
            await message.answer(f"Error: {e}")
            self.variable_manager.cancel(user_id)
