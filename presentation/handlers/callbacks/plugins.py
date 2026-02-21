"""
Plugin management callback handlers.

Handles plugin listing, marketplace, enable/disable operations.
"""

import logging
from typing import Optional

from aiogram.types import CallbackQuery

from presentation.handlers.callbacks.base import BaseCallbackHandler

logger = logging.getLogger(__name__)


class PluginCallbackHandler(BaseCallbackHandler):
    """Handler for plugin management callbacks."""

    async def handle_plugin_list(self, callback: CallbackQuery) -> None:
        """Show list of enabled plugins"""
        from presentation.keyboards.keyboards import Keyboards

        if not self.sdk_service:
            await callback.answer("⚠️ SDK not available")
            return

        plugins = self.sdk_service.get_enabled_plugins_info()

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

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=Keyboards.plugins_menu(plugins)
        )
        await callback.answer()

    async def handle_plugin_refresh(self, callback: CallbackQuery) -> None:
        """Refresh plugins list"""
        await callback.answer("🔄 Updated")
        await self.handle_plugin_list(callback)

    async def handle_plugin_marketplace(self, callback: CallbackQuery) -> None:
        """Show marketplace with available plugins"""
        from presentation.keyboards.keyboards import Keyboards

        if not self.sdk_service:
            await callback.answer("⚠️ SDK not available")
            return

        # All available plugins from official marketplace
        marketplace_plugins = [
            {"name": "commit-commands", "desc": "Git workflow: commit, push, PR"},
            {"name": "code-review", "desc": "Code review and PR"},
            {"name": "feature-dev", "desc": "Development of features with architecture"},
            {"name": "frontend-design", "desc": "Creation UI interfaces"},
            {"name": "ralph-loop", "desc": "RAFL: iterative problem solving"},
            {"name": "security-guidance", "desc": "Code security check"},
            {"name": "pr-review-toolkit", "desc": "Review tools PR"},
            {"name": "claude-code-setup", "desc": "Settings Claude Code"},
            {"name": "hookify", "desc": "Hook management"},
            {"name": "explanatory-output-style", "desc": "Explanatory inference style"},
            {"name": "learning-output-style", "desc": "Training output style"},
        ]

        # Get currently enabled plugins
        enabled = self.sdk_service.get_enabled_plugins_info()
        enabled_names = [p.get("name") for p in enabled]

        text = (
            "🛒 <b>Plugin Store</b>\n\n"
            "Select a plugin to enable:\n"
            "✅ - already enabled\n"
            "➕ - click to enable\n\n"
            "<i>Changes will take effect after restarting the bot</i>"
        )

        await callback.message.edit_text(
text,
            parse_mode="HTML",
            reply_markup=Keyboards.plugins_marketplace(marketplace_plugins, enabled_names)
        )
        await callback.answer()

    async def handle_plugin_info(self, callback: CallbackQuery) -> None:
        """Show plugin info"""
        parts = callback.data.split(":")
        plugin_name = parts[2] if len(parts) > 2 else "unknown"

        # Plugin descriptions
        descriptions = {
            "commit-commands": "Automation Git workflow: making commits, pushing, creating PR with correct formatting.",
            "code-review": "Professional code review: finds bugs, security issues, suggests improvements.",
            "feature-dev": "Step-by-step feature development: architecture analysis, planning, implementation.",
            "frontend-design": "Creating beautiful UI components and pages with modern design.",
            "ralph-loop": "RAFL (Reflect-Act-Fix-Loop): iterative solution of complex problems with self-testing.",
            "security-guidance": "Code Security Analysis: vulnerabilities, best practices, recommendations.",
            "pr-review-toolkit": "Review Tools Pull Request'ov on GitHub.",
            "claude-code-setup": "Setup and configuration Claude Code.",
            "hookify": "Creation and management git hooks.",
        }

        desc = descriptions.get(plugin_name, "Official plugin Claude Code")

        await callback.answer(f"ℹ️ {plugin_name}: {desc[:150]}", show_alert=True)

    async def handle_plugin_enable(self, callback: CallbackQuery) -> None:
        """Enable a plugin"""
        parts = callback.data.split(":")
        plugin_name = parts[2] if len(parts) > 2 else "unknown"

        if not self.sdk_service:
            await callback.answer("⚠️ SDK not available")
            return

        # Add plugin to enabled list
        if hasattr(self.sdk_service, 'add_plugin'):
            self.sdk_service.add_plugin(plugin_name)
            await callback.answer(f"✅ Plugin {plugin_name} included!")
            await self.handle_plugin_marketplace(callback)
        else:
            await callback.answer(
                f"ℹ️ Add {plugin_name} V CLAUDE_PLUGINS and restart the bot",
                show_alert=True
            )

    async def handle_plugin_disable(self, callback: CallbackQuery) -> None:
        """Disable a plugin"""
        parts = callback.data.split(":")
        plugin_name = parts[2] if len(parts) > 2 else "unknown"

        if not self.sdk_service:
            await callback.answer("⚠️ SDK not available")
            return

        # Remove plugin from enabled list
        if hasattr(self.sdk_service, 'remove_plugin'):
            self.sdk_service.remove_plugin(plugin_name)
            await callback.answer(f"❌ Plugin {plugin_name} disabled!")
            await self.handle_plugin_list(callback)
        else:
            await callback.answer(
                f"ℹ️ Remove {plugin_name} from CLAUDE_PLUGINS and restart the bot",
                show_alert=True
            )

    async def handle_plugin_close(self, callback: CallbackQuery) -> None:
        """Close plugins menu"""
        await callback.message.delete()
        await callback.answer()
