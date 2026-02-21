"""
Project Callback Handlers

Handles project management and file browser callbacks:
- Project selection, creation, deletion
- Folder browsing and navigation
- Working directory management
"""

import os
import re
import logging
import shutil
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from presentation.handlers.callbacks.base import BaseCallbackHandler
from presentation.keyboards.keyboards import CallbackData, Keyboards

logger = logging.getLogger(__name__)


class ProjectCallbackHandler(BaseCallbackHandler):
    """Handles project management callbacks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_states = {}  # For tracking mkdir input state

    def get_user_state(self, user_id: int) -> dict | None:
        """Get current user state if any."""
        return self._user_states.get(user_id)

    async def process_user_input(self, message) -> bool:
        """
        Process user input based on current state.
        Returns True if input was consumed, False otherwise.
        """
        user_id = message.from_user.id
        state = self._user_states.get(user_id)

        if not state:
            return False

        state_name = state.get("state")

        if state_name == "waiting_project_mkdir":
            return await self.handle_project_mkdir_input(message, message.text.strip())

        return False

    # ============== Project Selection ==============

    async def handle_project_select(self, callback: CallbackQuery) -> None:
        """Handle project selection."""
        data = CallbackData.parse_project_callback(callback.data)
        action = data.get("action")
        path = data.get("path", "")
        user_id = callback.from_user.id

        try:
            if action == "select" and path:
                # Set working directory
                if hasattr(self.message_handlers, 'set_working_dir'):
                    self.message_handlers.set_working_dir(user_id, path)

                await callback.message.edit_text(
                    f"📁 Working folder set:\n{path}",
                    parse_mode=None
                )
                await callback.answer(f"Project: {path}")

            elif action == "custom":
                # Prompt for custom path input
                if hasattr(self.message_handlers, 'set_expecting_path'):
                    self.message_handlers.set_expecting_path(user_id, True)

                await callback.message.edit_text(
                    "📂 Enter the project path:\n\nEnter the full path to the project folder.",
                    parse_mode=None
                )
                await callback.answer("Enter the path to chat")

        except Exception as e:
            logger.error(f"Error handling project select: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_project_switch(self, callback: CallbackQuery) -> None:
        """Handle project switch (from /change command)."""
        project_id = callback.data.split(":")[-1]
        user_id = callback.from_user.id

        if not self.project_service:
            await callback.answer("⚠️ Project service not available")
            return

        try:
            from domain.value_objects.user_id import UserId

            uid = UserId.from_int(user_id)
            project = await self.project_service.switch_project(uid, project_id)

            if project:
                # Also update working directory in message handlers
                if hasattr(self.message_handlers, 'set_working_dir'):
                    self.message_handlers.set_working_dir(user_id, project.working_dir)

                await callback.message.edit_text(
                    f"✅ Switched to project:\n\n"
                    f"{project.name}\n"
                    f"Path: {project.working_dir}\n\n"
                    f"Use /context list to view contexts.",
                    parse_mode=None
                )
                await callback.answer(f"Selected {project.name}")
            else:
                await callback.answer("❌ Project not found")

        except Exception as e:
            logger.error(f"Error switching project: {e}")
            await callback.answer(f"❌ Error: {e}")

    # ============== Project Creation ==============

    async def handle_project_create(self, callback: CallbackQuery) -> None:
        """Handle project create - show folder browser."""
        await self.handle_project_browse(callback)

    async def handle_project_browse(self, callback: CallbackQuery) -> None:
        """Handle project browse - show folders in /root/projects."""
        try:
            root_path = "/root/projects"

            # Check if path specified in callback
            if ":" in callback.data and callback.data.count(":") > 1:
                path = ":".join(callback.data.split(":")[2:])
                if path and os.path.isdir(path):
                    root_path = path

            # Ensure directory exists
            if not os.path.exists(root_path):
                os.makedirs(root_path, exist_ok=True)

            # Get folders
            folders = []
            try:
                for entry in os.scandir(root_path):
                    if entry.is_dir() and not entry.name.startswith('.'):
                        folders.append(entry.path)
            except OSError:
                pass

            folders.sort()

            if folders:
                text = (
                    f"📂 <b>Project overview</b>\n\n"
                    f"Path: <code>{root_path}</code>\n\n"
                    f"Select a folder to create the project:"
                )
            else:
                text = (
                    f"📂 <b>No folders found</b>\n\n"
                    f"Path: <code>{root_path}</code>\n\n"
                    f"First create a folder using Claude Code."
                )

            try:
                await callback.message.edit_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=Keyboards.folder_browser(folders, root_path)
                )
            except Exception as edit_err:
                # Ignore "message is not modified" error
                if "message is not modified" not in str(edit_err):
                    raise edit_err
            await callback.answer()

        except Exception as e:
            logger.error(f"Error browsing projects: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_project_folder(self, callback: CallbackQuery) -> None:
        """Handle folder selection - create project from folder."""
        folder_path = ":".join(callback.data.split(":")[2:])
        user_id = callback.from_user.id

        if not folder_path or not os.path.isdir(folder_path):
            await callback.answer("❌ Invalid folder")
            return

        if not self.project_service:
            await callback.answer("⚠️ Project service is not available")
            return

        try:
            from domain.value_objects.user_id import UserId

            uid = UserId.from_int(user_id)
            name = os.path.basename(folder_path)

            # Create or get project
            project = await self.project_service.get_or_create(uid, folder_path, name)

            # Switch to it
            await self.project_service.switch_project(uid, project.id)

            # Update working directory
            if hasattr(self.message_handlers, 'set_working_dir'):
                self.message_handlers.set_working_dir(user_id, folder_path)

            # Create keyboard with project actions
            project_created_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="📁 To the list of projects", callback_data="project:back"),
                    InlineKeyboardButton(text="📂 Main menu", callback_data="menu:main")
                ]
            ])

            await callback.message.edit_text(
                f"✅ <b>Project created:</b>\n\n"
                f"📁 {project.name}\n"
                f"📂 Path: <code>{project.working_dir}</code>\n\n"
                f"✨ Ready to go! Send your first message.\n\n"
                f"<i>Use the buttons below to navigate:</i>",
                parse_mode="HTML",
                reply_markup=project_created_keyboard
            )
            await callback.answer(f"✅ Created {project.name}")

        except Exception as e:
            logger.error(f"Error creating project from folder: {e}")
            await callback.answer(f"❌ Error: {e}")

    # ============== Folder Creation ==============

    async def handle_project_mkdir(self, callback: CallbackQuery) -> None:
        """Handle create folder - prompt for folder name."""
        user_id = callback.from_user.id

        # Set state to wait for folder name
        self._user_states[user_id] = {
            "state": "waiting_project_mkdir",
            "message_id": callback.message.message_id
        }

        text = (
            "📁 <b>Creating a Project Folder</b>\n\n"
            "Enter the name of the new folder:\n"
            "<i>(Latin, numbers, hyphen, underscore)</i>"
        )

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=Keyboards.menu_back_only("project:browse")
        )
        await callback.answer()

    async def handle_project_mkdir_input(self, message, folder_name: str) -> bool:
        """Process folder name input for project creation."""
        user_id = message.from_user.id

        # Validate folder name
        if not re.match(r'^[a-zA-Z0-9_-]+$', folder_name):
            await message.reply(
                "❌ Invalid folder name.\n"
                "Use only Latin characters, numbers, hyphens and underscores."
            )
            return True  # Consumed, but keep waiting

        folder_path = f"/root/projects/{folder_name}"

        if os.path.exists(folder_path):
            await message.reply(f"❌ Folder '{folder_name}' already exists.")
            return True

        try:
            os.makedirs(folder_path, exist_ok=True)

            # Clear state
            self._user_states.pop(user_id, None)

            # Create project from this folder
            if self.project_service:
                from domain.value_objects.user_id import UserId
                uid = UserId.from_int(user_id)
                project = await self.project_service.get_or_create(uid, folder_path, folder_name)
                await self.project_service.switch_project(uid, project.id)

                if hasattr(self.message_handlers, 'set_working_dir'):
                    self.message_handlers.set_working_dir(user_id, folder_path)

                # Create keyboard with project actions
                project_created_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="📁 To the list of projects", callback_data="project:back"),
                        InlineKeyboardButton(text="📂 Main menu", callback_data="menu:main")
                    ]
                ])

                await message.reply(
                    f"✅ <b>Project created:</b>\n\n"
                    f"📁 {folder_name}\n"
                    f"📂 Path: <code>{folder_path}</code>\n\n"
                    f"✨ Ready to go! Send your first message.\n\n"
                    f"<i>Use the buttons below to navigate:</i>",
                    parse_mode="HTML",
                    reply_markup=project_created_keyboard
                )
            else:
                await message.reply(f"✅ Folder created: <code>{folder_path}</code>", parse_mode="HTML")

            return True

        except Exception as e:
            logger.error(f"Error creating folder: {e}")
            await message.reply(f"❌ Folder creation error: {e}")
            return True

    # ============== Project Deletion ==============

    async def handle_project_delete(self, callback: CallbackQuery) -> None:
        """Handle project delete - show confirmation dialog."""
        project_id = callback.data.split(":")[-1]
        user_id = callback.from_user.id

        if not self.project_service:
            await callback.answer("⚠️ Project service not available")
            return

        try:
            from domain.value_objects.user_id import UserId

            uid = UserId.from_int(user_id)
            project = await self.project_service.get_by_id(project_id)

            if not project:
                await callback.answer("❌ Project not found")
                return

            if int(project.user_id) != user_id:
                await callback.answer("❌ This is not your project")
                return

            text = (
                f"⚠️ Deleting a project\n\n"
                f"Project: {project.name}\n"
                f"Path: {project.working_dir}\n\n"
                f"Select action:"
            )

            await callback.message.edit_text(
                text,
                parse_mode=None,
                reply_markup=Keyboards.project_delete_confirm(project_id, project.name)
            )
            await callback.answer()

        except Exception as e:
            logger.error(f"Error showing delete confirmation: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_project_delete_confirm(self, callback: CallbackQuery) -> None:
        """Handle confirmed project deletion."""
        # Parse callback: project:delete_confirm:<id>:<mode>
        parts = callback.data.split(":")
        project_id = parts[2] if len(parts) > 2 else ""
        delete_mode = parts[3] if len(parts) > 3 else "db"
        user_id = callback.from_user.id

        if not self.project_service:
            await callback.answer("⚠️ Project service not available")
            return

        try:
            from domain.value_objects.user_id import UserId

            uid = UserId.from_int(user_id)
            project = await self.project_service.get_by_id(project_id)

            if not project:
                await callback.answer("❌ Project not found")
                return

            if int(project.user_id) != user_id:
                await callback.answer("❌ This is not your project")
                return

            project_name = project.name
            project_path = project.working_dir

            # Delete from database
            deleted = await self.project_service.delete_project(uid, project_id)

            if not deleted:
                await callback.answer("❌ Failed to delete project")
                return

            # Delete files if requested
            files_deleted = False
            if delete_mode == "all":
                try:
                    if os.path.exists(project_path) and project_path.startswith("/root/projects"):
                        shutil.rmtree(project_path)
                        files_deleted = True
                except Exception as e:
                    logger.error(f"Error deleting project files: {e}")

            # Show result
            if files_deleted:
                result_text = (
                    f"✅ The project has been completely deleted\n\n"
                    f"Project: {project_name}\n"
                    f"Files deleted: {project_path}"
                )
            else:
                result_text = (
                    f"✅ The project has been removed from the database\n\n"
                    f"Project: {project_name}\n"
                    f"Files saved: {project_path}"
                )

            # Show updated project list
            projects = await self.project_service.list_projects(uid)
            current = await self.project_service.get_current(uid)
            current_id = current.id if current else None

            await callback.message.edit_text(
                result_text + "\n\n📁 Your projects:",
                parse_mode=None,
                reply_markup=Keyboards.project_list(projects, current_id, show_back=True, back_to="menu:projects")
            )
            await callback.answer(f"✅ Project {project_name} deleted")

        except Exception as e:
            logger.error(f"Error deleting project: {e}")
            await callback.answer(f"❌ Error: {e}")

    # ============== Navigation ==============

    async def handle_project_back(self, callback: CallbackQuery) -> None:
        """Handle back to project list."""
        user_id = callback.from_user.id

        if not self.project_service:
            await callback.answer("⚠️ Project service not available")
            return

        try:
            from domain.value_objects.user_id import UserId

            uid = UserId.from_int(user_id)
            projects = await self.project_service.list_projects(uid)
            current = await self.project_service.get_current(uid)
            current_id = current.id if current else None

            if projects:
                text = "📁 Your projects:\n\nSelect a project or create a new one:"
            else:
                text = "📁 No projects found\n\nCreate your first project:"

            await callback.message.edit_text(
                text,
                parse_mode=None,
                reply_markup=Keyboards.project_list(projects, current_id, show_back=True, back_to="menu:projects")
            )
            await callback.answer()

        except Exception as e:
            logger.error(f"Error going back to project list: {e}")
            await callback.answer(f"❌ Error: {e}")

    # ============== File Browser (cd:*) ==============

    async def handle_cd_goto(self, callback: CallbackQuery) -> None:
        """Handle folder navigation in /cd command."""
        import html
        # Extract path from callback data (cd:goto:/path/to/folder)
        path = callback.data.split(":", 2)[-1] if callback.data.count(":") >= 2 else ""

        if not self.file_browser_service:
            from application.services.file_browser_service import FileBrowserService
            self.file_browser_service = FileBrowserService()

        # Validate path is within root
        if not self.file_browser_service.is_within_root(path):
            await callback.answer("❌ Access denied")
            return

        # Check if directory exists
        if not os.path.isdir(path):
            await callback.answer("❌ Folder not found")
            return

        try:
            # Get content and tree view
            content = await self.file_browser_service.list_directory(path)
            tree_view = await self.file_browser_service.get_tree_view(path)

            # Update message
            await callback.message.edit_text(
                tree_view,
                parse_mode=ParseMode.HTML,
                reply_markup=Keyboards.file_browser(content)
            )
            await callback.answer()

        except Exception as e:
            logger.error(f"Error navigating to {path}: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_cd_root(self, callback: CallbackQuery) -> None:
        """Handle going to root directory."""
        if not self.file_browser_service:
            from application.services.file_browser_service import FileBrowserService
            self.file_browser_service = FileBrowserService()

        try:
            root_path = self.file_browser_service.ROOT_PATH

            # Ensure root exists
            os.makedirs(root_path, exist_ok=True)

            # Get content and tree view
            content = await self.file_browser_service.list_directory(root_path)
            tree_view = await self.file_browser_service.get_tree_view(root_path)

            # Update message
            await callback.message.edit_text(
                tree_view,
                parse_mode=ParseMode.HTML,
                reply_markup=Keyboards.file_browser(content)
            )
            await callback.answer("🏠 Root")

        except Exception as e:
            logger.error(f"Error going to root: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_cd_select(self, callback: CallbackQuery) -> None:
        """Handle selecting folder as working directory."""
        import html
        # Extract path from callback data (cd:select:/path/to/folder)
        path = callback.data.split(":", 2)[-1] if callback.data.count(":") >= 2 else ""
        user_id = callback.from_user.id

        if not self.file_browser_service:
            from application.services.file_browser_service import FileBrowserService
            self.file_browser_service = FileBrowserService()

        # Validate path
        if not self.file_browser_service.is_within_root(path):
            await callback.answer("❌ Access denied")
            return

        if not os.path.isdir(path):
            await callback.answer("❌ Folder not found")
            return

        try:
            # Set working directory
            if self.message_handlers:
                self.message_handlers.set_working_dir(user_id, path)

            # Create/switch project if project_service available
            project_name = os.path.basename(path) or "root"
            if self.project_service:
                from domain.value_objects.user_id import UserId
                uid = UserId.from_int(user_id)

                # First check if project with exact path exists
                existing = await self.project_service.project_repository.find_by_path(uid, path)
                if existing:
                    # Use existing project
                    project = existing
                else:
                    # Create new project for this exact path (don't use parent)
                    project = await self.project_service.create_project(uid, project_name, path)

                await self.project_service.switch_project(uid, project.id)
                project_name = project.name

            # Update message with confirmation
            await callback.message.edit_text(
                f"✅ <b>Working directory set</b>\n\n"
                f"<b>Path:</b> <code>{html.escape(path)}</code>\n"
                f"<b>Project:</b> {html.escape(project_name)}\n\n"
                f"Now all the teams Claude will be executed here.\n"
                f"Send a message to get started.",
                parse_mode=ParseMode.HTML
            )
            await callback.answer(f"✅ {project_name}")

        except Exception as e:
            logger.error(f"Error selecting folder {path}: {e}")
            await callback.answer(f"❌ Error: {e}")

    async def handle_cd_close(self, callback: CallbackQuery) -> None:
        """Handle closing the file browser."""
        try:
            await callback.message.delete()
            await callback.answer("Closed")
        except Exception as e:
            logger.error(f"Error closing file browser: {e}")
            await callback.answer("Closed")
