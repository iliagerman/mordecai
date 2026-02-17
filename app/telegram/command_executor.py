"""Telegram command execution module.

This module handles execution of parsed commands for the Telegram bot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.enums import CommandType, LogSeverity
from app.models.agent import ForgetMemoryResult

from app.services.conversation_service import ConversationService

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
        conversation_service: ConversationService | None = None,
        get_allowed_users: Callable | None = None,
    ):
        """Initialize the command executor.

        Args:
            agent_service: Agent service for session management.
            skill_service: Skill service for skill operations.
            logging_service: Logging service for activity logs.
            command_parser: Command parser instance.
            enqueue_callback: Callback to enqueue messages for agent processing.
            send_response_callback: Callback to send responses to user.
            conversation_service: Optional conversation service for multi-agent conversations.
            get_allowed_users: Optional callable returning allowed usernames (for agent listing).
        """
        self.agent_service = agent_service
        self.skill_service = skill_service
        self.logging_service = logging_service
        self.command_parser = command_parser
        self._enqueue_message = enqueue_callback
        self._send_response = send_response_callback
        self._conversation_service = conversation_service
        self._get_allowed_users = get_allowed_users

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

            case CommandType.CONVERSATION:
                await self.execute_conversation_command(user_id, chat_id, parsed)

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

    async def execute_cancel_command(self, user_id: str, chat_id: int) -> None:
        """Execute the 'cancel' command.

        Best-effort: attempts to cancel any currently running tool/process for the user.
        """

        try:
            msg = await self.agent_service.cancel_current_work(user_id)
        except Exception as e:
            logger.exception("Cancel failed for user %s", user_id)
            await self._send_response(chat_id, f"Failed to cancel current work: {e}")
            return

        await self._send_response(chat_id, msg)

        try:
            await self.logging_service.log_action(
                user_id=user_id,
                action="Requested cancellation",
                severity=LogSeverity.INFO,
            )
        except Exception:
            logger.exception("Failed to log cancel action")

    async def execute_conversation_command(
        self,
        user_id: str,
        chat_id: int,
        parsed,
    ) -> None:
        """Execute conversation command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            parsed: Parsed command with CONVERSATION type.
        """
        if not parsed.args:
            await self._send_response(
                chat_id,
                "Usage: conversation <action> [args]\n"
                "Actions:\n"
                "  create <topic> [max_iterations] [@agent1 @agent2] [-- instructions]\n"
                "  join <conversation_id>\n"
                "  add <conversation_id> @agent\n"
                "  instruct <instructions for your agent>\n"
                "  cancel <conversation_id>\n"
                "  status <conversation_id>\n"
                "  list\n"
                "  agents",
            )
            return

        action = parsed.args[0].lower() if parsed.args else ""
        args = parsed.args[1:] if len(parsed.args) > 1 else []

        if action == "create":
            await self._execute_conversation_create(user_id, chat_id, args)
        elif action == "join":
            await self._execute_conversation_join(user_id, chat_id, args)
        elif action == "add":
            await self._execute_conversation_add(user_id, chat_id, args)
        elif action == "cancel":
            await self._execute_conversation_cancel(user_id, chat_id, args)
        elif action == "status":
            await self._execute_conversation_status(user_id, chat_id, args)
        elif action == "instruct":
            await self._execute_conversation_instruct(user_id, chat_id, args)
        elif action == "list":
            await self._execute_conversation_list(user_id, chat_id)
        elif action == "agents":
            await self._execute_conversation_agents(user_id, chat_id)
        else:
            await self._send_response(chat_id, f"Unknown conversation action: {action}")

    async def _execute_conversation_create(
        self,
        user_id: str,
        chat_id: int,
        args: list[str],
    ) -> None:
        """Execute conversation create command.

        Expected format (after 'create' is stripped):
            <topic_words...> [max_iterations] [timeout=SECONDS] [@agent1 @agent2 ...] [-- instructions...]

        - Topic: all leading words that are not @mentions, numbers, or timeout=N
        - max_iterations: first bare integer (default 5)
        - timeout=N: instruction wait timeout in seconds (default 300 = 5 min)
        - @mentions: agent usernames to invite
        - Everything after '--' is treated as agent instructions

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
            args: Command arguments (after 'create').
        """
        if not args:
            await self._send_response(
                chat_id,
                "Usage: conversation create <topic> [max_iterations] [timeout=SECONDS] "
                "[@agent1 @agent2] [-- instructions]",
            )
            return

        max_iterations = 5
        instruction_timeout = 300  # 5 minutes default
        participant_usernames: list[str] = []
        topic_parts: list[str] = []
        agent_instructions: str | None = None

        # Check for instructions after '--'
        if "--" in args:
            separator_idx = args.index("--")
            instruction_parts = args[separator_idx + 1 :]
            args = args[:separator_idx]
            if instruction_parts:
                agent_instructions = " ".join(instruction_parts)

        # Parse remaining args
        for arg in args:
            if arg.startswith("@"):
                participant_usernames.append(arg.lstrip("@").lower())
            elif arg.lower().startswith("timeout="):
                try:
                    instruction_timeout = int(arg.split("=", 1)[1])
                except ValueError:
                    pass
            elif arg.isdigit() and not topic_parts:
                max_iterations = int(arg)
            elif arg.isdigit() and topic_parts:
                max_iterations = int(arg)
            else:
                topic_parts.append(arg)

        topic = " ".join(topic_parts).strip()
        if not topic:
            await self._send_response(chat_id, "Please provide a topic for the conversation.")
            return

        if not self._conversation_service:
            await self._send_response(
                chat_id,
                f"🔄 Conversation service is not available. "
                f"Parsed: topic='{topic}', iterations={max_iterations}, "
                f"agents={participant_usernames or 'none'}",
            )
            return

        try:
            conversation_id = await self._conversation_service.create_conversation(
                creator_user_id=user_id,
                creator_chat_id=chat_id,
                topic=topic,
                max_iterations=max_iterations,
                participant_user_ids=participant_usernames or None,
                agent_instructions=agent_instructions,
                instruction_timeout=instruction_timeout,
            )
            await self._send_response(
                chat_id,
                f"Conversation created!\n"
                f"ID: {conversation_id}\n"
                f"Topic: {topic}\n"
                f"Max iterations: {max_iterations}\n"
                f"Agents: {', '.join(participant_usernames) if participant_usernames else 'None specified'}\n"
                f"Use 'conversation status {conversation_id}' for a live transcript.",
            )
        except Exception as e:
            logger.exception("Failed to create conversation")
            await self._send_response(chat_id, f"❌ Failed to create conversation: {e}")

    async def _execute_conversation_join(
        self,
        user_id: str,
        chat_id: int,
        args: list[str],
    ) -> None:
        """Execute conversation join command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
            args: Command arguments.
        """
        if not args:
            await self._send_response(chat_id, "Usage: conversation join <conversation_id>")
            return

        if not self._conversation_service:
            await self._send_response(chat_id, "Conversation service is not available.")
            return

        conversation_id = args[0]
        try:
            success = await self._conversation_service.add_participant(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if success:
                await self._send_response(chat_id, f"✅ Joined conversation {conversation_id}.")
            else:
                await self._send_response(
                    chat_id,
                    f"❌ Could not join conversation {conversation_id}. "
                    "It may not exist or is no longer active.",
                )
        except Exception as e:
            logger.exception("Failed to join conversation")
            await self._send_response(chat_id, f"❌ Failed to join: {e}")

    async def _execute_conversation_add(
        self,
        user_id: str,
        chat_id: int,
        args: list[str],
    ) -> None:
        """Execute conversation add agent command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
            args: Command arguments.
        """
        if len(args) < 2:
            await self._send_response(chat_id, "Usage: conversation add <conversation_id> @agent")
            return

        if not self._conversation_service:
            await self._send_response(chat_id, "Conversation service is not available.")
            return

        conversation_id = args[0]
        agent_mention = args[1]

        if not agent_mention.startswith("@"):
            await self._send_response(chat_id, "Agent must be specified with @username")
            return

        agent_username = agent_mention.lstrip("@").lower()

        try:
            success = await self._conversation_service.add_participant(
                conversation_id=conversation_id,
                user_id=agent_username,
            )
            if success:
                await self._send_response(
                    chat_id,
                    f"✅ Added @{agent_username} to conversation {conversation_id}.",
                )
            else:
                await self._send_response(
                    chat_id,
                    f"❌ Could not add @{agent_username}. "
                    "Conversation may not exist, is inactive, or they already joined.",
                )
        except Exception as e:
            logger.exception("Failed to add participant")
            await self._send_response(chat_id, f"❌ Failed to add agent: {e}")

    async def _execute_conversation_cancel(
        self,
        user_id: str,
        chat_id: int,
        args: list[str],
    ) -> None:
        """Execute conversation cancel command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
            args: Command arguments.
        """
        if not args:
            await self._send_response(chat_id, "Usage: conversation cancel <conversation_id>")
            return

        if not self._conversation_service:
            await self._send_response(chat_id, "Conversation service is not available.")
            return

        conversation_id = args[0]
        try:
            success = await self._conversation_service.cancel_conversation(conversation_id)
            if success:
                await self._send_response(chat_id, f"✅ Conversation {conversation_id} cancelled.")
            else:
                await self._send_response(
                    chat_id,
                    f"❌ Could not cancel conversation {conversation_id}. "
                    "It may not exist or is already ended.",
                )
        except Exception as e:
            logger.exception("Failed to cancel conversation")
            await self._send_response(chat_id, f"❌ Failed to cancel: {e}")

    async def _execute_conversation_status(
        self,
        user_id: str,
        chat_id: int,
        args: list[str],
    ) -> None:
        """Show a live transcript for a conversation.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
            args: Command arguments (expects conversation_id).
        """
        if not args:
            await self._send_response(chat_id, "Usage: conversation status <conversation_id>")
            return

        if not self._conversation_service:
            await self._send_response(chat_id, "Conversation service is not available.")
            return

        conversation_id = args[0]
        try:
            transcript = await self._conversation_service.get_conversation_transcript(conversation_id)
            await self._send_response(chat_id, transcript)
        except Exception as e:
            logger.exception("Failed to get conversation transcript")
            await self._send_response(chat_id, f"Failed to get transcript: {e}")

    async def _execute_conversation_instruct(
        self,
        user_id: str,
        chat_id: int,
        args: list[str],
    ) -> None:
        """Provide instructions for the user's agent in an active conversation.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
            args: Instruction text tokens.
        """
        if not args:
            await self._send_response(
                chat_id,
                "Usage: conversation instruct <your instructions for the agent>",
            )
            return

        if not self._conversation_service:
            await self._send_response(chat_id, "Conversation service is not available.")
            return

        instruction = " ".join(args)
        try:
            result = await self._conversation_service.handle_private_instruction(
                user_id=user_id,
                instruction=instruction,
            )
            if result:
                await self._send_response(chat_id, result)
            else:
                await self._send_response(
                    chat_id,
                    "You don't have an active conversation to instruct.",
                )
        except Exception as e:
            logger.exception("Failed to store conversation instruction")
            await self._send_response(chat_id, f"Failed to store instruction: {e}")

    async def _execute_conversation_list(
        self,
        user_id: str,
        chat_id: int,
    ) -> None:
        """Execute conversation list command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
        """
        if not self._conversation_service:
            await self._send_response(chat_id, "Conversation service is not available.")
            return

        active = await self._conversation_service.get_active_conversation_for_user(user_id)
        if not active:
            await self._send_response(chat_id, "You have no active conversations.")
            return

        state = active["state"]
        lines = [
            "📋 Your active conversations:\n",
            f"  ID: {active['conversation_id']}",
            f"  Topic: {state.topic}",
            f"  Iteration: {state.current_iteration or 0}/{state.max_iterations}",
        ]
        await self._send_response(chat_id, "\n".join(lines))

    async def _execute_conversation_agents(
        self,
        user_id: str,
        chat_id: int,
    ) -> None:
        """List available agents (allowed users) that can be invited to conversations.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID.
        """
        if not self._get_allowed_users:
            await self._send_response(chat_id, "Agent listing is not available.")
            return

        allowed = list(self._get_allowed_users())
        if not allowed:
            await self._send_response(
                chat_id,
                "No allowed users configured. All users can be invited.",
            )
            return

        lines = ["🤖 Available agents (users):\n"]
        for username in sorted(allowed):
            marker = " (you)" if username.lower() == user_id.lower() else ""
            lines.append(f"  @{username}{marker}")
        lines.append("\nUse @username when creating a conversation to invite them.")
        await self._send_response(chat_id, "\n".join(lines))

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
