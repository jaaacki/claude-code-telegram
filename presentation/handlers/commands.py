import logging
import os
from aiogram import Router, F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.enums import ParseMode
from application.services.bot_service import BotService
from infrastructure.claude_code.proxy_service import ClaudeCodeProxyService
from infrastructure.claude_code.diagnostics import run_diagnostics, format_diagnostics_for_telegram
from presentation.keyboards.keyboards import Keyboards

logger = logging.getLogger(__name__)

# Claude Code plugin commands that should be passed through to SDK/CLI
# These are NOT Telegram bot commands - they are Claude Code slash commands
CLAUDE_SLASH_COMMANDS = {
    "ralph-loop", "cancel-ralph",  # ralph-loop plugin
    "commit", "commit-push-pr", "clean_gone",  # commit-commands plugin
    "code-review", "review-pr",  # code-review plugin
    "feature-dev",  # feature-dev plugin
    "frontend-design",  # frontend-design plugin
    "plan", "explore",  # built-in agent commands
}
router = Router()


class CommandHandlers:
    """Bot command handlers for Claude Code proxy"""

    def __init__(
        self,
        bot_service: BotService,
        claude_proxy: ClaudeCodeProxyService,
        message_handlers=None,  # Optional, set after initialization
        project_service=None,   # ProjectService for /change
        context_service=None,   # ContextService for /context
        file_browser_service=None,  # FileBrowserService for /cd
        account_service=None  # AccountService for language
    ):
        self.bot_service = bot_service
        self.claude_proxy = claude_proxy
        self.message_handlers = message_handlers
        self.project_service = project_service
        self.context_service = context_service
        self.file_browser_service = file_browser_service
        self.account_service = account_service

    async def start(self, message: Message) -> None:
        """Handle /start command - show main inline menu"""
        user = await self.bot_service.get_or_create_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        # Access denied - user not in ALLOWED_USER_ID whitelist
        if user is None:
            await message.answer(
                "🚫 <b>Access Denied</b>\n\n"
                "You are not authorized to use this bot.\n"
                f"Your Telegram ID: <code>{message.from_user.id}</code>\n\n"
                "<i>Contact the bot administrator to request access.</i>",
                parse_mode="HTML"
            )
            logger.warning(f"Access denied for user {message.from_user.id} (@{message.from_user.username})")
            return

        user_id = message.from_user.id

        # Check if user has language set (first launch detection)
        user_lang = None
        if self.account_service:
            user_lang = await self.account_service.get_user_language(user_id)

        # If no language set, show language selection first
        if not user_lang or user_lang == "":
            await message.answer(
                "🌐 <b>Select language / Select language / 选择语言</b>",
                parse_mode="HTML",
                reply_markup=Keyboards.language_select()
            )
            return

        # Load translator for user's language
        from shared.i18n import get_translator
        t = get_translator(user_lang)

        # Get working directory and project info
        working_dir = "/root"
        project_name = None
        if self.message_handlers:
            working_dir = self.message_handlers.get_working_dir(user_id)

        # Get current project
        if self.project_service:
            try:
                from domain.value_objects.user_id import UserId
                uid = UserId.from_int(user_id)
                project = await self.project_service.get_current(uid)
                if project:
                    project_name = project.name
                    working_dir = project.working_dir
            except Exception:
                pass

        # Get YOLO status
        yolo_enabled = False
        if self.message_handlers:
            yolo_enabled = self.message_handlers.is_yolo_mode(user_id)

        # Check if task running
        has_task = False
        if self.message_handlers and hasattr(self.message_handlers, 'sdk_service'):
            if self.message_handlers.sdk_service:
                has_task = self.message_handlers.sdk_service.is_task_running(user_id)
        if not has_task:
            has_task = self.claude_proxy.is_task_running(user_id)

        # Build status text using translations
        project_info = t("start.project", name=project_name) if project_name else t("start.no_project")
        path_info = f"📁 <code>{working_dir}</code>"

        status_parts = [project_info, path_info]
        if yolo_enabled:
            status_parts.append(t("start.yolo_on"))
        if has_task:
            status_parts.append(t("start.task_running"))

        text = (
            f"🤖 <b>Claude Code Telegram</b>\n\n"
            f"{t('start.greeting', name=user.first_name)}\n\n"
            f"{chr(10).join(status_parts)}\n\n"
            f"<i>{t('start.ready')}</i>"
        )

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=Keyboards.main_menu_inline(
                working_dir=working_dir,
                project_name=project_name,
                yolo_enabled=yolo_enabled,
                has_active_task=has_task,
                lang=user_lang
            )
        )

    async def help(self, message: Message) -> None:
        """Handle /help command"""
        help_text = """
🤖 <b>Claude Code Telegram Proxy - Reference</b>

<b>Navigation and projects:</b>
/cd - Folder navigation
/change - Change project
/fresh - Clear context

<b>Context Management:</b>
/context new - Create a new context
/context list - List of contexts
/context clear - Clear current context
/vars - Managing Context Variables

<b>Claude Code:</b>
/yolo - YOLO mode (auto-confirm)
/plugins - Show plugins
/cancel - Cancel task
/status - Status Claude Code

<b>Monitoring:</b>
/metrics - System metrics (CPU, RAM, disk)
/docker - List Docker containers

<b>Basic Commands:</b>
/start - Launch bot
/help - Show help
/stats - Your statistics
/clear - Clear chat history

<b>How does this work:</b>
1. Send the task by message
2. Claude Code will start working
3. You will see the output in real time
4. Confirm/reject transactions
5. Answer questions Claude

<b>HITL (Human-in-the-Loop):</b>
🔐 <b>Permissions</b> - Confirmation of hazardous operations
❓ <b>Questions</b> - Answers to questions Claude
🛑 <b>Cancel</b> - Stop a task at any time

<b>Examples:</b>
• "Create Python script that outputs hello"
• "Read the file README.md"
• "Launch npm install in the project"
• "Fix the bug in main.py"

Just describe what needs to be done!
        """
        await message.answer(help_text, parse_mode="HTML")

    async def clear(self, message: Message) -> None:
        """Handle /clear command"""
        await self.bot_service.clear_session(message.from_user.id)
        await message.answer("🧹 Chat history cleared!")

    async def stats(self, message: Message) -> None:
        """Handle /stats command"""
        stats = await self.bot_service.get_user_stats(message.from_user.id)

        # Build command stats safely
        by_status = stats.get('commands', {}).get('by_status', {})
        status_lines = [f"  • {k}: {v}" for k, v in by_status.items() if k != 'total']
        status_text = "\n".join(status_lines) if status_lines else "  No data"

        text = f"""📊 <b>Your statistics</b>

<b>User:</b> {stats.get('user', {}).get('username', 'Unknown')}
<b>Role:</b> {stats.get('user', {}).get('role', 'user')}
<b>Status:</b> {'✅ Active' if stats.get('user', {}).get('is_active') else '❌ Inactive'}

<b>Teams:</b>
• Total: {stats.get('commands', {}).get('total', 0)}
{status_text}

<b>Sessions:</b>
• Total: {stats.get('sessions', {}).get('total', 0)}
• Active: {stats.get('sessions', {}).get('active', 0)}"""
        await message.answer(text, parse_mode="HTML")

    async def metrics(self, message: Message) -> None:
        """Handle /metrics command and 📊 Metrics button"""
        info = await self.bot_service.get_system_info()

        metrics = info["metrics"]
        lines = [
            "📊 <b>System metrics</b>",
            "",
            f"💻 <b>CPU:</b> {metrics['cpu_percent']:.1f}%",
            f"🧠 <b>Memory:</b> {metrics['memory_percent']:.1f}% ({metrics['memory_used_gb']}GB / {metrics['memory_total_gb']}GB)",
            f"💾 <b>Disk:</b> {metrics['disk_percent']:.1f}% ({metrics['disk_used_gb']}GB / {metrics['disk_total_gb']}GB)",
        ]

        if metrics.get('load_average', [0])[0] > 0:
            lines.append(f"📈 <b>Load:</b> {metrics['load_average'][0]:.2f}")

        # Show alerts
        if info.get("alerts"):
            lines.append("\n⚠️ <b>Warnings:</b>")
            lines.extend(info["alerts"])

        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=Keyboards.system_metrics(show_back=True, back_to="menu:system"))

    async def docker(self, message: Message) -> None:
        """Handle /docker command and 🐳 Docker button"""
        try:
            from infrastructure.monitoring.system_monitor import create_system_monitor
            monitor = create_system_monitor()
            containers = await monitor.get_docker_containers()

            if not containers:
                await message.answer(
                    "🐳 <b>Docker containers</b>\n\n"
                    "No containers found.\n\n"
                    "Use Claude Code for management Docker:\n"
                    "• 'docker ps -a'\n"
                    "• 'docker run ...'",
                    parse_mode="HTML"
                )
                return

            # Build container list with action buttons
            lines = ["🐳 <b>Docker containers:</b>\n"]
            for c in containers:
                status_emoji = "🟢" if c["status"] == "running" else "🔴"
                lines.append(f"\n{status_emoji} <b>{c['name']}</b>")
                lines.append(f"   Status: {c['status']}")
                lines.append(f"   Image: <code>{c['image'][:30]}</code>")

            text = "\n".join(lines)
            await message.answer(
                text,
                parse_mode="HTML",
                reply_markup=Keyboards.docker_list(containers, show_back=True, back_to="menu:system")
            )

        except Exception as e:
            logger.error(f"Error getting docker containers: {e}")
            await message.answer(
                f"🐳 Docker\n\n❌ Error: {e}",
                parse_mode=None
            )

    async def project(self, message: Message, command: CommandObject) -> None:
        """Handle /project command - set working directory"""
        user_id = message.from_user.id

        if command.args:
            # Set working directory directly
            path = command.args.strip()

            # Validate path exists (basic check)
            if not os.path.isabs(path):
                path = os.path.abspath(path)

            if self.message_handlers:
                self.message_handlers.set_working_dir(user_id, path)
                await message.answer(
                    f"📁 <b>Working folder set:</b>\n<code>{path}</code>",
                    parse_mode="HTML"
                )
            else:
                await message.answer(
                    "⚠️ Message handlers are not initialized",
                    parse_mode=None
                )
        else:
            # Show current working directory and prompt for input
            current_dir = "/root"
            if self.message_handlers:
                current_dir = self.message_handlers.get_working_dir(user_id)

            # List some common project directories
            projects = []
            for dir_path in ["/root", "/home", "/var/www", "/opt"]:
                if os.path.exists(dir_path):
                    projects.append({"name": os.path.basename(dir_path) or dir_path, "path": dir_path})

            await message.answer(
                f"📁 <b>Current working folder:</b>\n<code>{current_dir}</code>\n\n"
                f"Use `/project <path>` for a change.\n\n"
                f"Example:\n<code>/project /home/myproject</code>",
                parse_mode="HTML",
                reply_markup=Keyboards.project_selection(projects) if projects else None
            )

    async def change(self, message: Message) -> None:
        """Handle /change command - show project list for switching"""
        user_id = message.from_user.id

        if not self.project_service:
            await message.answer("⚠️ The project service is not initialized")
            return

        from domain.value_objects.user_id import UserId
        uid = UserId.from_int(user_id)

        # Get user's projects
        projects = await self.project_service.list_projects(uid)
        current = await self.project_service.get_current(uid)

        current_name = current.name if current else "No"
        current_id = current.id if current else None

        if projects:
            text = (
                f"📂 <b>Change project</b>\n\n"
                f"Current: <b>{current_name}</b>\n\n"
                f"Select a project:"
            )
            keyboard = Keyboards.project_list(projects, current_id, show_back=True, back_to="menu:projects")
        else:
            text = (
                f"📂 <b>No projects</b>\n\n"
                f"You don't have any projects yet.\n"
                f"Create a new one or open `/root/projects`"
            )
            keyboard = Keyboards.project_list([], None, show_create=True, show_back=True, back_to="menu:projects")

        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    async def context(self, message: Message, command: CommandObject) -> None:
        """Handle /context command - show interactive context menu"""
        user_id = message.from_user.id

        if not self.project_service or not self.context_service:
            await message.answer("⚠️ Services are not initialized")
            return

        from domain.value_objects.user_id import UserId
        uid = UserId.from_int(user_id)

        # Get current project
        project = await self.project_service.get_current(uid)
        if not project:
            await message.answer(
                "❌ No active project\n\n"
                "Use /change to select a project.",
                parse_mode=None
            )
            return

        # Get current context
        current_ctx = await self.context_service.get_current(project.id)
        ctx_name = current_ctx.name if current_ctx else "not selected"
        msg_count = current_ctx.message_count if current_ctx else 0
        has_session = current_ctx.has_session if current_ctx else False

        # Build status text
        session_status = "📜 There is a session" if has_session else "✨ Clean"
        text = (
            f"💬 Managing Contexts\n\n"
            f"📂 Project: {project.name}\n"
            f"💬 Context: {ctx_name}\n"
            f"📝 Messages: {msg_count}\n"
            f"📌 Status: {session_status}"
        )

        keyboard = Keyboards.context_menu(ctx_name, project.name, msg_count, show_back=True, back_to="menu:context")
        await message.answer(text, parse_mode=None, reply_markup=keyboard)

    async def fresh(self, message: Message) -> None:
        """
        Handle /fresh command - create new context for fresh conversation.

        Creates a new context and switches to it, ensuring:
        - New Claude session (no memory of previous conversation)
        - Clean message history
        - Old contexts remain available for switching back
        """
        user_id = message.from_user.id

        # Clear internal session cache
        if self.message_handlers:
            self.message_handlers.clear_session_cache(user_id)

        # Create new context in project
        if self.project_service and self.context_service:
            from domain.value_objects.user_id import UserId
            uid = UserId.from_int(user_id)

            project = await self.project_service.get_current(uid)
            if project:
                # Create new context (auto-generated name, set as current)
                new_context = await self.context_service.create_new(
                    project_id=project.id,
                    user_id=uid,
                    name=None,  # Auto-generate name
                    set_as_current=True
                )

                await message.answer(
                    f"✅ New context created!\n\n"
                    f"📂 Project: {project.name}\n"
                    f"💬 Context: {new_context.name}\n\n"
                    f"Start a new conversation.",
                    parse_mode=None
                )
                return

        # No project/context - just clear bot service session
        await self.bot_service.clear_session(user_id)
        await message.answer(
            "🧹 Session cleared!\n\n"
            "The next message will start a new conversation.",
            parse_mode=None
        )

    async def yolo(self, message: Message) -> None:
        """
        Handle /yolo command - toggle YOLO mode.

        YOLO mode auto-approves all operations without waiting for confirmation.
        Use with caution!
        """
        import asyncio

        user_id = message.from_user.id

        if not self.message_handlers:
            await message.answer("⚠️ Message handlers are not initialized")
            return

        current = self.message_handlers.is_yolo_mode(user_id)
        new_state = not current
        self.message_handlers.set_yolo_mode(user_id, new_state)

        if new_state:
            response = await message.answer(
                "🚀 <b>YOLO Mode: ON</b> ⚡",
                parse_mode="HTML"
            )
        else:
            response = await message.answer(
                "🛡️ <b>YOLO Mode: OFF</b>",
                parse_mode="HTML"
            )

        # Delete command and response after 2 seconds
        async def delete_messages():
            await asyncio.sleep(2)
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await response.delete()
            except Exception:
                pass

        asyncio.create_task(delete_messages())

    async def cd(self, message: Message, command: CommandObject) -> None:
        """
        Handle /cd command - interactive folder navigation.

        Usage:
            /cd           - Show current directory with navigation
            /cd ..        - Go to parent directory
            /cd <folder>  - Navigate to folder
            /cd ~         - Go to root (/root/projects)
        """
        user_id = message.from_user.id

        if not self.file_browser_service:
            # Fallback: create service on demand
            from application.services.file_browser_service import FileBrowserService
            self.file_browser_service = FileBrowserService()

        # Get current working directory
        current_dir = "/root/projects"
        if self.message_handlers:
            current_dir = self.message_handlers.get_working_dir(user_id)

        # Ensure current_dir is within root
        if not self.file_browser_service.is_within_root(current_dir):
            current_dir = self.file_browser_service.ROOT_PATH

        # Resolve target path
        if command.args:
            target = command.args.strip()
            target_path = self.file_browser_service.resolve_path(current_dir, target)
        else:
            target_path = current_dir

        # Ensure directory exists
        if not os.path.isdir(target_path):
            # Try creating if it's a subdir of root
            if self.file_browser_service.is_within_root(target_path):
                try:
                    os.makedirs(target_path, exist_ok=True)
                except OSError:
                    target_path = self.file_browser_service.ROOT_PATH
            else:
                target_path = self.file_browser_service.ROOT_PATH

        # Get directory content and tree view
        content = await self.file_browser_service.list_directory(target_path)
        tree_view = await self.file_browser_service.get_tree_view(target_path)

        # Send with HTML formatting
        await message.answer(
            tree_view,
            parse_mode=ParseMode.HTML,
            reply_markup=Keyboards.file_browser(content)
        )

    async def cancel(self, message: Message) -> None:
        """Handle /cancel command - cancel running Claude Code task"""
        user_id = message.from_user.id
        cancelled = False

        # Try SDK first (preferred) - it handles full cleanup including status reset
        if self.message_handlers and hasattr(self.message_handlers, 'sdk_service'):
            sdk_service = self.message_handlers.sdk_service
            if sdk_service:
                cancelled = await sdk_service.cancel_task(user_id)
                if cancelled:
                    await message.answer("🛑 <b>Task canceled</b> (SDK)", parse_mode="HTML")
                    return

        # Try CLI fallback
        if self.claude_proxy:
            cli_cancelled = await self.claude_proxy.cancel_task(user_id)
            if cli_cancelled:
                await message.answer("🛑 <b>Task canceled</b> (CLI)", parse_mode="HTML")
                return

        if not cancelled:
            await message.answer("ℹ️ There are currently no running tasks")

    async def status(self, message: Message) -> None:
        """Handle /status command - show Claude Code status"""
        user_id = message.from_user.id

        # Check if Claude Code CLI is installed
        installed, version_info = await self.claude_proxy.check_claude_installed()

        # Check SDK availability
        sdk_status = "❌ Not available"
        sdk_running = False
        if self.message_handlers and hasattr(self.message_handlers, 'sdk_service'):
            sdk_service = self.message_handlers.sdk_service
            if sdk_service:
                sdk_ok, sdk_msg = await sdk_service.check_sdk_available()
                sdk_status = "🟢 Available (HITL included)" if sdk_ok else f"🔴 {sdk_msg}"
                sdk_running = sdk_service.is_task_running(user_id)

        # Check if task is running (either backend)
        cli_running = self.claude_proxy.is_task_running(user_id)
        is_running = sdk_running or cli_running

        # Get working directory
        working_dir = "/root"
        if self.message_handlers:
            working_dir = self.message_handlers.get_working_dir(user_id)

        cli_emoji = "🟢" if installed else "🔴"
        task_status = "🔄 Works" if is_running else "⏸️ Expectation"

        # Determine backend in use
        backend = "SDK" if sdk_running else ("CLI" if cli_running else "Expectation")

        text = f"""
📊 <b>Status Claude Code</b>

<b>CLI:</b> {cli_emoji} {version_info}
<b>SDK:</b> {sdk_status}
<b>Task:</b> {task_status} ({backend})
<b>Working folder:</b> <code>{working_dir}</code>
"""

        if is_running:
            text += "\n\nUse /cancel to stop the current task."

        text += "\n\nUse /diagnose for complete diagnostics."

        await message.answer(text, parse_mode="HTML")

    async def diagnose(self, message: Message) -> None:
        """Handle /diagnose command - run full Claude Code diagnostics"""
        await message.answer("🔍 Running diagnostics... (may take up to 30 seconds)")

        try:
            results = await run_diagnostics(self.claude_proxy.claude_path)
            text = format_diagnostics_for_telegram(results)
            await message.answer(text, parse_mode=None)
        except Exception as e:
            await message.answer(f"❌ Diagnostics failed: {e}")

    async def claude_command_passthrough(self, message: Message, command: CommandObject) -> None:
        """
        Handle Claude Code slash commands by passing them to SDK/CLI.

        Commands like /ralph-loop, /commit, /code-review are Claude Code commands
        that should be executed by Claude, not by the Telegram bot.

        IMPORTANT: We send the command as "run /<command>" instead of just "/<command>"
        because the slash prefix alone is interpreted as a local CLI macro that expands
        but doesn't trigger an API call. By saying "run", we instruct Claude to invoke
        the Skill tool which actually executes the skill/plugin.

        Supports reply to file - file content will be added to the command context.
        """
        user_id = message.from_user.id
        command_name = command.command  # e.g., "ralph-loop"

        logger.info(f"[{user_id}] Claude Code command passthrough: /{command_name}")

        # Build the prompt to invoke the skill via Claude's Skill tool
        # We say "run /command" so Claude knows to invoke the Skill tool,
        # rather than treating it as a local CLI macro
        skill_command = f"/{command_name}"
        if command.args:
            skill_command += f" {command.args}"

        # Instruct Claude to run the skill
        prompt = f"run {skill_command}"

        # Check if message handlers are available
        if not self.message_handlers:
            await message.answer(
                "⚠️ Message handlers are not initialized.\n"
                "I can't send the command Claude Code.",
                parse_mode=None
            )
            return

        # Check for reply to file - add file context to command
        reply = message.reply_to_message
        file_info = ""
        if reply and self.message_handlers.file_processor_service:
            # Check if reply message has a cached file
            if reply.message_id in self.message_handlers._file_cache:
                processed_file = self.message_handlers._file_cache.pop(reply.message_id)
                prompt = self.message_handlers.file_processor_service.format_for_prompt(
                    processed_file, prompt
                )
                file_info = f"\n📎 File: {processed_file.filename}"
                logger.info(f"[{user_id}] Added cached file to command: {processed_file.filename}")

            # Check if reply message has document/photo
            elif reply.document or reply.photo:
                file_context = await self.message_handlers._extract_reply_file_context(
reply, message.bot
                )
                if file_context:
                    processed_file, _ = file_context
                    prompt = self.message_handlers.file_processor_service.format_for_prompt(
                        processed_file, prompt
                    )
                    file_info = f"\n📎 File: {processed_file.filename}"
                    logger.info(f"[{user_id}] Added reply file to command: {processed_file.filename}")

        # Inform user that command is being passed through
        await message.answer(
            f"🔌 <b>Plugin command:</b> <code>{skill_command}</code>{file_info}\n\n"
            f"I pass it on to Claude Code...",
            parse_mode="HTML"
        )

        # Pass the command to handle_text with prompt_override and force_new_session
        # Plugin commands need a fresh session, not resume of previous conversation
        await self.message_handlers.handle_text(
            message,
            prompt_override=prompt,
            force_new_session=True
        )

    async def plugins(self, message: Message) -> None:
        """
        Handle /plugins command - show and manage Claude Code plugins.

        Displays list of enabled plugins with ability to:
        - View plugin info
        - Enable/disable plugins
        - Browse marketplace for new plugins
        """
        if not self.message_handlers or not hasattr(self.message_handlers, 'sdk_service'):
            await message.answer("⚠️ SDK the service is not initialized")
            return

        sdk_service = self.message_handlers.sdk_service
        if not sdk_service:
            await message.answer("⚠️ SDK service not available")
            return

        # Get enabled plugins info
        plugins = sdk_service.get_enabled_plugins_info()

        if not plugins:
            text = (
                "🔌 <b>Plugins Claude Code</b>\n\n"
                "No active plugins.\n\n"
                "Click 🛒 <b>Shop</b> to add plugins."
            )
        else:
            text = "🔌 <b>Plugins Claude Code</b>\n\n"
            for p in plugins:
                name = p.get("name", "unknown")
                desc = p.get("description", "")
                source = p.get("source", "official")
                available = p.get("available", True)

                status = "✅" if available else "⚠️"
                source_icon = "🌐" if source == "official" else "📁"
                text += f"{status} {source_icon} <b>{name}</b>\n"
                if desc:
                    text += f"   <i>{desc}</i>\n"

            text += f"\n<i>Total: {len(plugins)} plugins</i>"

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=Keyboards.plugins_menu(plugins)
        )

    async def vars(self, message: Message, command: CommandObject) -> None:
        """
        Handle /vars command - manage context variables with interactive menu.

        Usage:
            /vars                          - show interactive menu
            /vars set NAME value [desc]    - set a variable (legacy)
            /vars del NAME                 - delete a variable (legacy)

        Variables are automatically included in Claude's context.
        Description helps Claude understand how to use the variable.
        """
        user_id = message.from_user.id

        if not self.project_service or not self.context_service:
            await message.answer("⚠️ Services are not initialized")
            return

        from domain.value_objects.user_id import UserId
        from presentation.keyboards.keyboards import Keyboards
        uid = UserId.from_int(user_id)

        # Get current project and context
        project = await self.project_service.get_current(uid)
        if not project:
            await message.answer(
                "❌ No active project\n\n"
                "Use /change to select a project.",
                parse_mode=None
            )
            return

        context = await self.context_service.get_current(project.id)
        if not context:
            await message.answer(
                "❌ No active context\n\n"
                "Use /context to create context.",
                parse_mode=None
            )
            return

        args = command.args.strip() if command.args else ""

        # No args - show interactive menu
        if not args:
            variables = await self.context_service.get_variables(context.id)

            if variables:
                lines = [f"📋 Context Variables\n"]
                lines.append(f"📂 {project.name} / {context.name}\n")
                for name in sorted(variables.keys()):
                    var = variables[name]
                    # Mask long values
                    display = var.value[:8] + "***" if len(var.value) > 8 else var.value
                    lines.append(f"• {name} = {display}")
                    if var.description:
                        lines.append(f"  ↳ {var.description[:50]}")
                text = "\n".join(lines)
            else:
                text = (
                    f"📋 Context Variables\n\n"
                    f"📂 {project.name} / {context.name}\n\n"
                    f"No variables yet.\n"
                    f"Click ➕ Add to create."
                )

            keyboard = Keyboards.variables_menu(variables, project.name, context.name, show_back=True, back_to="menu:context")
            await message.answer(text, parse_mode=None, reply_markup=keyboard)
            return

        # Parse action (legacy text commands)
        parts = args.split(maxsplit=3)
        action = parts[0].lower()

        if action == "set":
            if len(parts) < 3:
                await message.answer(
                    "❌ Usage: /vars set NAME value [description]",
                    parse_mode=None
                )
                return

            name = parts[1].upper()  # Variable names are uppercase
            value = parts[2]
            description = parts[3] if len(parts) > 3 else ""

            await self.context_service.set_variable(context.id, name, value, description)
            resp = f"✅ Variable set: {name}\n"
            if description:
                resp += f"Description: {description}\n"
            resp += f"\nClaude will use it automatically."
            await message.answer(resp, parse_mode=None)
            return

        if action == "del" or action == "delete":
            if len(parts) < 2:
                await message.answer(
                    "❌ Usage: /vars del NAME",
                    parse_mode=None
                )
                return

            name = parts[1].upper()
            deleted = await self.context_service.delete_variable(context.id, name)

            if deleted:
                await message.answer(
                    f"🗑 Variable deleted: {name}",
                    parse_mode=None
                )
            else:
                await message.answer(
                    f"⚠️ Variable {name} not found",
                    parse_mode=None
                )
            return

        # Unknown action
        await message.answer(
            "❌ Unknown team\n\n"
            "Use /vars for interactive menu\n"
            "or legacy teams:\n"
            "/vars set NAME value [desc] - install\n"
            "/vars del NAME - delete",
            parse_mode=None
        )


    async def test_question(self, message: Message) -> None:
        """Test AskUserQuestion keyboard - shows sample question with inline buttons"""
        user_id = message.from_user.id

        # Sample options like Claude would send
        options = [
            "Python + FastAPI",
            "Node.js + Express",
            "Go + Gin",
            "Rust + Actix"
        ]

        request_id = "test123"

        await message.answer(
            "<b>❓ Test question from Claude</b>\n\n"
            "Which technology stack to use for API?\n\n"
            "<i>Select an option or enter your own:</i>",
            parse_mode="HTML",
            reply_markup=Keyboards.claude_question(user_id, options, request_id)
        )


def register_handlers(router: Router, handlers: CommandHandlers) -> None:
    """
    Register command handlers.

    Only /start and /cancel are registered as Telegram commands.
    All other functionality is accessed via the inline menu system.
    """
    # Main command - shows the inline menu
    router.message.register(handlers.start, Command("start"))

    # Emergency cancel command (always available)
    router.message.register(handlers.cancel, Command("cancel"))

    # YOLO mode toggle
    router.message.register(handlers.yolo, Command("yolo"))

    # Test command for AskUserQuestion keyboard
    router.message.register(handlers.test_question, Command("test_question"))

    # Claude Code plugin commands passthrough
    # These are forwarded to Claude Code SDK/CLI instead of being handled by bot
    for cmd in CLAUDE_SLASH_COMMANDS:
        router.message.register(
            handlers.claude_command_passthrough,
            Command(cmd)
        )
