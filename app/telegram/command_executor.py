"""Telegram command execution module.

This module handles execution of parsed commands for the Telegram bot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.enums import CommandType, LogSeverity
from app.models.agent import ForgetMemoryResult

if TYPE_CHECKING:
    from app.services.agent_service import AgentService
    from app.services.command_parser import ParsedCommand
    from app.services.logging_service import LoggingService
    from app.services.skill_service import SkillService

logger = logging.getLogger(__name__)


class CommandExecutor:
    """Handles execution of parsed commands for the Telegram bot.

    Routes commands to the appropriate handler based on type and
    manages responses back to the user.
    """

    def __init__(
        self,
        agent_service: AgentService,
        skill_service: SkillService,
        logging_service: LoggingService,
        command_parser: Any,
        enqueue_callback: Callable,
        send_response_callback: Callable,
    ):
        """Initialize the command executor.

        Args:
            agent_service: Agent service for session management.
            skill_service: Skill service for skill operations.
            logging_service: Logging service for activity logs.
            command_parser: Command parser instance.
            enqueue_callback: Callback to enqueue messages for agent processing.
            send_response_callback: Callback to send responses to user.
        """
        self.agent_service = agent_service
        self.skill_service = skill_service
        self.logging_service = logging_service
        self.command_parser = command_parser
        self._enqueue_message = enqueue_callback
        self._send_response = send_response_callback

    async def execute_command(
        self,
        parsed: ParsedCommand,
        user_id: str,
        chat_id: int,
        original_message: str,
        onboarding_context: dict[str, str | None] | None = None,
    ) -> None:
        """Execute a parsed command.

        Routes the command to the appropriate handler based on type.

        Args:
            parsed: Parsed command with type and arguments.
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            original_message: Original message text.
            onboarding_context: Optional onboarding context (soul.md, id.md content)
                if this is the user's first interaction.

        Requirements:
            - 11.5: Support basic commands
            - 11.6: Parse and execute appropriate actions
        """
        match parsed.command_type:
            case CommandType.NEW:
                await self.execute_new_command(user_id, chat_id)

            case CommandType.LOGS:
                await self.execute_logs_command(user_id, chat_id)

            case CommandType.HELP:
                help_text = self.command_parser.get_help_text()
                await self._send_response(chat_id, help_text)

            case CommandType.INSTALL_SKILL:
                if parsed.args:
                    await self.execute_install_skill(user_id, chat_id, parsed.args[0])
                else:
                    await self._send_response(
                        chat_id,
                        "Please provide a URL: install skill <url>",
                    )

            case CommandType.UNINSTALL_SKILL:
                if parsed.args:
                    await self.execute_uninstall_skill(user_id, chat_id, parsed.args[0])
                else:
                    await self._send_response(
                        chat_id,
                        "Please provide a skill name: uninstall skill <name>",
                    )

            case CommandType.FORGET:
                query = parsed.args[0] if parsed.args else ""
                await self.execute_forget_command(user_id, chat_id, query=query, delete=False)

            case CommandType.FORGET_DELETE:
                query = parsed.args[0] if parsed.args else ""
                await self.execute_forget_command(user_id, chat_id, query=query, delete=True)

            case CommandType.MESSAGE:
                # Forward to agent via SQS queue (with onboarding context if first interaction)
                await self._enqueue_message(user_id, chat_id, original_message, onboarding_context)

    async def execute_forget_command(
        self,
        user_id: str,
        chat_id: int,
        *,
        query: str,
        delete: bool,
    ) -> None:
        """Deterministically forget (delete) long-term memories.

        This intentionally avoids relying on the LLM to call tools correctly.

        Usage:
        - forget <query> => dry-run preview
        - forget! <query> => delete
        """

        q = (query or "").strip()
        if not q:
            await self._send_response(
                chat_id, "Usage: forget <query> (or forget! <query> to delete)"
            )
            return

        memory_service = getattr(self.agent_service, "memory_service", None)
        if memory_service is None:
            await self._send_response(
                chat_id,
                "Long-term memory is not available right now (memory service not initialized).",
            )
            return

        try:
            res: ForgetMemoryResult = memory_service.delete_similar_records(
                user_id=user_id,
                query=q,
                memory_type="all",
                similarity_threshold=0.7,
                dry_run=not delete,
                max_matches=10,
            )
        except Exception as e:
            await self._send_response(chat_id, f"Failed to forget memory: {e}")
            return

        if res.matched == 0:
            await self._send_response(chat_id, f"No matching memories found for '{q}'.")
            return

        lines: list[str] = []
        if res.dry_run:
            lines.append(f"Matches: {res.matched}. Dry-run (no deletions).")
        else:
            lines.append(f"Matches: {res.matched}. Deleted: {res.deleted}.")
        lines.append("")
        for m in res.matches:
            lines.append(
                f"- [{m.namespace}] {m.text_preview} (score={m.score:.2f}, id={m.memory_record_id})"
            )
        if res.dry_run:
            lines.append("")
            lines.append("To actually delete these, send: forget! " + q)

        await self._send_response(chat_id, "\n".join(lines))

    async def execute_new_command(self, user_id: str, chat_id: int) -> None:
        """Execute the 'new' command to create a fresh session.

        Triggers memory extraction before clearing the session to preserve
        important information in long-term memory.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.

        Requirements:
            - 11.5: Support basic commands (new)
            - 4.1: Invoke MemoryExtractionService before clearing
            - 4.4: Inform user that conversation was analyzed
        """
        logger.info("Creating new session for user %s", user_id)

        # Create new session via agent service (triggers extraction)
        _, notification = await self.agent_service.new_session(user_id)

        await self._send_response(chat_id, notification)

        # Log the action
        try:
            await self.logging_service.log_action(
                user_id=user_id,
                action="Started new session",
                severity=LogSeverity.INFO,
            )
        except Exception:
            logger.exception("Failed to log new session action")

    async def execute_logs_command(self, user_id: str, chat_id: int) -> None:
        """Execute the 'logs' command to show recent activity.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.

        Requirements:
            - 11.5: Support basic commands (logs)
        """
        logger.debug("Fetching logs for user %s", user_id)

        logs = await self.logging_service.get_recent_logs(user_id, hours=24)

        if not logs:
            await self._send_response(chat_id, "No recent activity logs found.")
            return

        # Format logs for display
        log_lines = ["📋 Recent Activity (last 24 hours):\n"]
        for log_entry in logs[:20]:  # Limit to 20 entries
            timestamp = log_entry.timestamp.strftime("%H:%M:%S")
            severity_emoji = self._get_severity_emoji(log_entry.severity)
            log_lines.append(f"{severity_emoji} [{timestamp}] {log_entry.action}")

        await self._send_response(chat_id, "\n".join(log_lines))

    def _get_severity_emoji(self, severity: LogSeverity) -> str:
        """Get emoji for log severity level.

        Args:
            severity: Log severity level.

        Returns:
            Emoji string for the severity.
        """
        match severity:
            case LogSeverity.DEBUG:
                return "🔍"
            case LogSeverity.INFO:
                return "ℹ️"
            case LogSeverity.WARNING:
                return "⚠️"
            case LogSeverity.ERROR:
                return "❌"
            case _:
                return "📝"

    async def execute_install_skill(self, user_id: str, chat_id: int, url: str) -> None:
        """Execute the 'install skill' command.

        Skills are always installed to the user's personal folder, never to shared.
        This ensures isolation between users - each user has their own copy of
        installed skills.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            url: URL to download the skill from.

        Requirements:
            - 11.5: Support basic commands (install skill)
        """
        from app.services.skill_service import SkillInstallError

        logger.info("Downloading skill from %s to pending for user %s", url, user_id)

        await self._send_response(chat_id, f"⏳ Downloading skill to pending/ from {url}...")

        try:
            res = self.skill_service.download_skill_to_pending(url, user_id, scope="user")
            metadata = res.get("metadata")

            if metadata is None:
                await self._send_response(
                    chat_id,
                    "❌ Skill download succeeded but no metadata was returned.",
                )
                logger.error(
                    "SkillService.download_skill_to_pending returned no metadata (user_id=%s, url=%s)",
                    user_id,
                    url,
                )
                return

            await self._send_response(
                chat_id,
                (
                    f"✅ Skill '{metadata.name}' downloaded to pending successfully!\n"
                    f"Pending path: {res.get('pending_dir')}\n\n"
                    "Next:\n"
                    "1) Review the pending skill's SKILL.md and scripts\n"
                    "2) Run onboarding (AI review required)\n"
                    '   - onboard_pending_skills(scope="user", ai_review_completed=true)\n'
                ),
            )

            # Log the action
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Downloaded skill to pending: {metadata.name}",
                severity=LogSeverity.INFO,
                details={
                    "url": url,
                    "skill_name": metadata.name,
                    "pending_dir": res.get("pending_dir"),
                },
            )

        except SkillInstallError as e:
            error_msg = f"❌ Failed to install skill: {str(e)}"
            await self._send_response(chat_id, error_msg)

            # Log the error
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Failed to install skill from {url}",
                severity=LogSeverity.ERROR,
                details={"url": url, "error": str(e)},
            )

    async def execute_uninstall_skill(self, user_id: str, chat_id: int, skill_name: str) -> None:
        """Execute the 'uninstall skill' command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            skill_name: Name of the skill to uninstall.

        Requirements:
            - 11.5: Support basic commands (uninstall skill)
        """
        from app.services.skill_service import SkillNotFoundError

        logger.info("Uninstalling skill %s for user %s", skill_name, user_id)

        try:
            result = self.skill_service.uninstall_skill(skill_name, user_id)
            await self._send_response(chat_id, f"✅ {result}")

            # Log the action
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Uninstalled skill: {skill_name}",
                severity=LogSeverity.INFO,
                details={"skill_name": skill_name},
            )

        except SkillNotFoundError as e:
            await self._send_response(chat_id, f"❌ {str(e)}")

            # Log the error
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Failed to uninstall skill: {skill_name}",
                severity=LogSeverity.WARNING,
                details={"skill_name": skill_name, "error": str(e)},
            )
