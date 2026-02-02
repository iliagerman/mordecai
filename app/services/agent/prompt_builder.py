from __future__ import annotations

import logging
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.models.agent import AttachmentInfo, MemoryContext
from app.services.agent.skills import SkillRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SystemPromptBuilder:
    config: Any
    skill_repo: SkillRepository
    personality_service: Any
    working_dir_resolver: Callable[[str], Path]
    obsidian_stm_cache: MutableMapping[str, str]
    user_agent_names: MutableMapping[str, str | None]
    has_cron: bool

    def build(
        self,
        user_id: str,
        memory_context: MemoryContext | None = None,
        attachments: list[AttachmentInfo] | None = None,
        onboarding_context: dict[str, str | None] | None = None,
    ) -> str:
        memory_context = memory_context or MemoryContext()

        # Cached name first, then fallback to memory context
        agent_name = self.user_agent_names.get(user_id)
        if not agent_name:
            agent_name = memory_context.agent_name
            if agent_name:
                self.user_agent_names[user_id] = agent_name
                logger.info("Loaded agent name '%s' from memory for user %s", agent_name, user_id)

        facts = memory_context.facts or []
        preferences = memory_context.preferences or []

        identity = self._identity_section(agent_name, facts=facts, preferences=preferences)

        # Date/time
        try:
            tz = ZoneInfo(self.config.timezone)
        except Exception:
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        current_datetime = now.strftime("%A, %B %d, %Y at %I:%M %p %Z")

        prompt = (
            "You are a helpful AI assistant with access to tools.\n\n"
            f"## Current Date and Time\n\n{current_datetime}\n\n"
            f"## Identity\n\n{identity}\n"
        )

        # Add onboarding welcome section if this is the first interaction
        if onboarding_context:
            prompt += self._onboarding_section(onboarding_context)

        prompt += self._personality_section(user_id)
        prompt += self._obsidian_access_section()
        prompt += self._obsidian_stm_section(user_id)

        if getattr(self.config, "memory_enabled", False):
            prompt += self._memory_capabilities_section()

        if facts or preferences:
            prompt += self._retrieved_memory_section(facts=facts, preferences=preferences)

        prompt += self._commands_section()
        prompt += self._skills_section(user_id)
        prompt += self._working_folder_section(user_id)

        if self.has_cron:
            prompt += self._scheduling_section()

        prompt += self._attachment_context(attachments, user_id)

        return prompt

    def _identity_section(
        self,
        agent_name: str | None,
        *,
        facts: list[Any],
        preferences: list[Any],
    ) -> str:
        if agent_name:
            return (
                f"YOUR NAME IS {agent_name.upper()}.\n"
                f"Always identify yourself as {agent_name}.\n"
                f"When asked your name, say: 'I'm {agent_name}.'.\n"
                "NEVER say you are Claude, ChatGPT, or any generic AI.\n"
            )

        has_name_in_memory = any(
            "assistant" in str(f).lower()
            and any(word in str(f).lower() for word in ["call", "name", "mordecai", "jarvis"])
            for f in facts + preferences
        )
        if has_name_in_memory:
            return (
                "Check the Retrieved Memory section below - it may contain your name that the user previously gave you.\n"
                "If you find your name there, use it to identify yourself.\n"
                "NEVER say you are Claude, ChatGPT, or any generic AI.\n"
            )

        return (
            "You do not have a name yet.\n"
            "When asked your name, respond:\n"
            "'I don't have a name yet. What would you like to call me?'\n"
            "NEVER say you are Claude, ChatGPT, or any generic AI.\n"
            "When a user gives you a name, use the set_agent_name tool to store it in memory so you remember it across sessions.\n"
            "IMPORTANT: If the set_agent_name tool returns an error, do NOT claim you will remember the name. Be honest that memory storage failed and you can only use the name for this session.\n"
        )

    def _personality_section(self, user_id: str) -> str:
        if not getattr(self.config, "personality_enabled", True):
            return ""
        if not self.personality_service.is_enabled():
            return ""

        docs = self.personality_service.load(user_id)
        if not docs:
            return ""

        lines: list[str] = []
        lines.append("## Personality (Obsidian Vault)\n")
        lines.append(
            "The following files are loaded from repo defaults and/or the configured Obsidian vault and must be followed as system-level instructions.\n"
        )

        if "soul" in docs:
            soul = docs["soul"]
            lines.append(f"### soul.md (source: {soul.source})\n")
            lines.append(f"Path: `{soul.path}`\n")
            lines.append(soul.content)
            lines.append("")

        if "id" in docs:
            ident = docs["id"]
            lines.append(f"### id.md (source: {ident.source})\n")
            lines.append(f"Path: `{ident.path}`\n")
            lines.append(ident.content)
            lines.append("")

        lines.append("")
        return "\n".join(lines)

    def _obsidian_access_section(self) -> str:
        # Obsidian vault access capabilities
        try:
            vault_root_raw = getattr(self.config, "obsidian_vault_root", None)
            vault_root_path = (
                Path(str(vault_root_raw)).expanduser().resolve() if vault_root_raw else None
            )
            vault_accessible = bool(
                vault_root_path and vault_root_path.exists() and vault_root_path.is_dir()
            )
            vault_root_display = f"`{vault_root_path}`" if vault_root_path else "(not configured)"
        except Exception:
            vault_root_raw = getattr(self.config, "obsidian_vault_root", None)
            vault_accessible = False
            vault_root_display = f"`{vault_root_raw}`" if vault_root_raw else "(not configured)"

        out = "\n## Obsidian Vault Access\n\n"
        if not vault_root_raw:
            out += (
                "Obsidian vault root is **not configured**. You do NOT have filesystem access to the user's Obsidian notes. "
                "Do not claim you can read or write Obsidian. Ask the user to paste content or send files as attachments.\n\n"
                "If/when Obsidian access is enabled, prefer a **bounded search** (limited depth/results) rather than scanning an entire vault.\n\n"
            )
        elif not vault_accessible:
            out += (
                f"Obsidian vault root is configured as {vault_root_display}, but it is **not accessible in this runtime** (missing path / not mounted / no permissions). "
                "Do not claim you can read the user's vault. Ask the user to paste the relevant note contents (or attach the file), or fix the deployment so the vault is mounted and readable.\n\n"
                "If the vault becomes accessible later, use a **bounded search** (limit depth/results; avoid scanning the entire vault) when locating notes by keyword.\n\n"
            )
        else:
            out += (
                f"Obsidian vault root is accessible at {vault_root_display}.\n\n"
                "Constraints and best practices:\n"
                "- You may safely read/write ONLY the per-user personality/identity files under `me/<USER_ID>/{soul.md,id.md}` via the personality tools.\n"
                "- You may use the injected STM scratchpad (`me/<USER_ID>/stm.md`) as context when it appears in this prompt.\n"
                "- If the user asks for content that likely exists in the vault but does not provide an exact path, you SHOULD perform a bounded search in the most relevant folder(s) (e.g. `family/` and/or `me/<USER_ID>/`).\n"
                "  - Keep searches bounded: limit depth, limit results (e.g. first 20), avoid scanning the entire vault.\n"
                "  - Prefer filename-based search first; if ambiguous, ask a clarifying question.\n"
                "  - After finding candidate files, read them (file_read) until you find the requested content, or ask the user to confirm which file is correct.\n"
                "  - Example bounded search commands (via shell):\n"
                "    - `find <VAULT_ROOT>/family -maxdepth 4 -type f -iname '*.md' -print | head -n 50`\n"
                "    - `find <VAULT_ROOT>/family -maxdepth 4 -type f \\( -iname '*<keyword>*' -o -iname '*<keyword2>*' \\) -print | head -n 20`\n"
                '    - `rg -n --max-count 20 -S "<keyword>|<keyword2>" <VAULT_ROOT>/family 2>/dev/null || true`\n\n'
            )
        return out

    def _obsidian_stm_section(self, user_id: str) -> str:
        vault_root = getattr(self.config, "obsidian_vault_root", None)
        if not vault_root:
            return ""

        try:
            from app.observability.redaction import redact_text
            from app.tools.short_term_memory_vault import read_raw_text

            stm_text = read_raw_text(
                vault_root,
                user_id,
                max_chars=getattr(self.config, "personality_max_chars", 20_000),
            )

            if not stm_text:
                stm_text = self.obsidian_stm_cache.get(user_id)
            else:
                self.obsidian_stm_cache[user_id] = stm_text

            if not stm_text:
                return ""

            safe_stm = redact_text(
                stm_text,
                max_chars=getattr(self.config, "personality_max_chars", 20_000),
            )

            return (
                "\n## Short-Term Memory (Obsidian)\n\n"
                "The following content comes from the user's Obsidian STM scratchpad. "
                "It may include recent session summaries and notes that are not yet reliably "
                "available via long-term memory retrieval.\n\n"
                "Use it as context for this session. If it conflicts with other info, "
                "prefer newer statements and ask clarifying questions.\n\n"
                f"{safe_stm.strip()}\n\n"
            )
        except Exception:
            return ""

    def _memory_capabilities_section(self) -> str:
        return (
            "## Memory Capabilities\n\n"
            "You have two types of memory:\n\n"
            "1. **Session Memory**: Conversation history within this session (automatically managed).\n\n"
            "2. **Long-Term Memory (persistent memory)**: Facts and preferences about the user that persist across sessions.\n\n"
            "**Tools:**\n"
            "- `set_agent_name`: Store your name when the user gives you one.\n"
            "- `remember_fact`: Store an explicit fact the user asked you to remember.\n"
            "- `remember_preference`: Store an explicit preference the user asked you to remember.\n"
            "- `remember`: Convenience wrapper (fact vs preference).\n"
            "- `search_memory`: Search your long-term memory when the user asks about past conversations, their preferences, or facts you've learned about them.\n\n"
            "- `forget_memory`: Remove incorrect/outdated long-term memories (use dry-run first; then delete if confirmed).\n\n"
            "**Critical honesty rule:** Do NOT claim you 'deleted/cleared/forgot' long-term memory unless you actually called `forget_memory` with `dry_run=false` and it reported deletions.\n"
            "- Running shell commands (including `echo`, `rm`, etc.) cannot modify long-term memory.\n"
            "- If you only performed a dry-run, you must explicitly say no deletions were performed.\n\n"
            "**Important:** When the user explicitly says 'remember ...' or 'please remember ...', store it immediately using `remember_fact` or `remember_preference`.\n\n"
            "Use `search_memory` when the user asks things like:\n"
            "- 'What do you know about me?'\n"
            "- 'What are my preferences?'\n"
            "- 'Do you remember when I told you...?'\n"
            "- 'What have we discussed before?'\n\n"
        )

    def _retrieved_memory_section(self, *, facts: list[Any], preferences: list[Any]) -> str:
        out = "## Retrieved Memory\n\n"
        out += "The following information was retrieved from your long-term memory based on the current conversation:\n\n"
        if facts:
            out += "**Facts about the user:**\n"
            for fact in facts[:5]:
                out += f"- {fact}\n"
            out += "\n"
        if preferences:
            out += "**User preferences:**\n"
            for pref in preferences[:5]:
                out += f"- {pref}\n"
            out += "\n"
        return out

    def _commands_section(self) -> str:
        commands = getattr(self.config, "agent_commands", None)
        if not commands:
            return ""

        lines = ["## Available Commands\n"]
        for cmd in commands:
            name = cmd.get("name", "")
            desc = cmd.get("description", "")
            lines.append(f"- {name}: {desc}")
        lines.append("")
        return "\n".join(lines)

    def _skills_section(self, user_id: str) -> str:
        skills_info = self.skill_repo.discover(user_id)
        if not skills_info:
            return ""

        prompt = "\n## Installed Skills\n\n"
        prompt += (
            "Skills contain instructions with bash commands to execute.\n\n"
            "**Your available tools:**\n"
            '- `shell(command="...")` - Run bash/shell commands\n'
            '- `file_read(path="...", mode="view")` - Read files\n'
            "- `file_write(...)` - Write files\n\n"
            "**To use a skill:**\n"
            "1. file_read the SKILL.md ONCE\n"
            "2. Extract the bash commands from the instructions\n"
            '3. Run them with shell(command="the bash command")\n\n'
            "IMPORTANT: Do NOT claim that a file/config exists or is correctly configured unless you have verified it.\n"
            "- Verification options: successful file_read that returns content, or shell preflight like `test -f <path>` / `command -v <bin>`.\n"
            "- If a file_read tool call returns empty content / no content, treat it as NOT verified.\n\n"
            "IMPORTANT: If a command depends on an env var you set earlier, inline it explicitly in the command you run "
            '(example: `FOO="$FOO" some-cli ...`). Do not assume env propagation will work automatically.\n\n'
            "IMPORTANT: When passing commands to shell(), write shell quoting literally.\n"
            '- ✅ Use `export VAR="/abs/path"` (quotes included, no backslashes).\n'
            '- ❌ Do NOT write `export VAR=\\"/abs/path\\"` (this sets the env var to a value that includes quote characters).\n\n'
            "**CRITICAL:** After reading SKILL.md, your next tool call is usually shell(), not file_read. "
            "However, if the skill has missing setup requirements (see 'Skill Setup Required'), you MUST ask the user and persist the values first (set_skill_env_vars / set_skill_config) before running shell commands for that skill.\n\n"
        )

        for skill in skills_info:
            name = skill.name or "unknown"
            desc = skill.description or ""
            path = skill.path or ""
            prompt += f"- **{name}**: {desc}\n"
            prompt += f'  → file_read(path="{path}/SKILL.md", mode="view") → shell(command="...")\n'

        missing = self.skill_repo.get_missing_skill_requirements(user_id)
        if missing:
            prompt += "\n## Skill Setup Required\n\n"
            prompt += (
                "Some installed have missing prerequisites (env vars, config fields, binaries, or config files).\n"
                "If a required value is missing, ask the user for it and then persist it:\n"
                "- Env vars: `set_skill_env_vars(...)`\n"
                "- Config fields: `set_skill_config(...)` (stored in `skills/<user>/skills_secrets.yml`)\n"
                "- Missing binaries: The user needs to install them or they may already be in the skill's venv\n"
                "- Missing config files: Run skill onboarding to generate them from templates\n\n"
                "IMPORTANT: If a skill has missing setup requirements, do NOT run its shell commands yet.\n\n"
                "Examples:\n"
                '- set_skill_env_vars(skill_name="himalaya", env_json=\'{"HIMALAYA_CONFIG":"/path/to/himalaya.toml"}\', apply_to="user")\n'
                '- set_skill_config(skill_name="himalaya", config_json=\'{"GMAIL":"user@gmail.com","PASSWORD":"app-password"}\', apply_to="user")\n'
                "- onboard_pending_skills() to re-run onboarding and generate config files\n\n"
                "Missing values (do NOT guess these):\n"
            )
            for skill_name, reqs in missing.items():
                prompt += f"- **{skill_name}**\n"
                env_reqs = reqs.env or []
                cfg_reqs = reqs.config or []
                bins_reqs = reqs.bins or []
                config_files_reqs = reqs.config_files or []
                if env_reqs:
                    prompt += "  - env:\n"
                    for r in env_reqs:
                        n = r.name
                        if not n:
                            continue
                        line = f"    - {n}"
                        if r.prompt:
                            line += f" — {r.prompt}"
                        if r.example:
                            line += f" (example: {r.example})"
                        prompt += line + "\n"
                if cfg_reqs:
                    prompt += "  - config:\n"
                    for r in cfg_reqs:
                        n = r.name
                        if not n:
                            continue
                        line = f"    - {n}"
                        if r.prompt:
                            line += f" — {r.prompt}"
                        if r.example:
                            line += f" (example: {r.example})"
                        prompt += line + "\n"
                if bins_reqs:
                    prompt += "  - bins (required executables not found):\n"
                    for r in bins_reqs:
                        n = r.name
                        if not n:
                            continue
                        line = f"    - {n}"
                        if r.prompt:
                            line += f" — {r.prompt}"
                        prompt += line + "\n"
                if config_files_reqs:
                    prompt += "  - config_files (rendered config files missing):\n"
                    for r in config_files_reqs:
                        n = r.name
                        if not n:
                            continue
                        line = f"    - {n}"
                        if r.prompt:
                            line += f" — {r.prompt}"
                        prompt += line + "\n"

        return prompt

    def _working_folder_section(self, user_id: str) -> str:
        working_dir = self.working_dir_resolver(user_id)
        wd = working_dir
        out = "\n## Working Folder\n\n"
        out += f"**Your working folder is: `{working_dir}`**\n\n"
        out += (
            "**CRITICAL: Use this folder for ALL file operations:**\n"
            f"- When creating files, use full path: `{wd}/<filename>`\n"
            f"- When saving output, save to: `{wd}/<filename>`\n"
            "- NEVER create files in `.` or any other location\n"
            "- NEVER use relative paths without the working folder prefix\n\n"
            "**CRITICAL: For shell commands, ALWAYS set work_dir:**\n"
            f'- shell(command="...", work_dir="{wd}")\n'
            "- This ensures commands run in your working folder\n"
            "- Output files will be saved in the correct location\n\n"
            "Examples:\n"
            f'- ✅ Correct: `file_write(path="{wd}/out.txt", ...)`\n'
            f'- ✅ Correct: `file_write(path="{wd}/data.json", ...)`\n'
            f'- ✅ Correct: `shell(command="uv run ...", work_dir="{wd}")`\n'
            '- ❌ Wrong: `file_write(path="output.txt", ...)`\n'
            '- ❌ Wrong: `file_write(path="./data.json", ...)`\n'
            '- ❌ Wrong: `shell(command="...")` (missing work_dir)\n\n'
            "You have read and write access to this directory.\n"
        )
        return out

    def _scheduling_section(self) -> str:
        return (
            "\n## Scheduling Capabilities\n\n"
            "You can create, list, and delete scheduled tasks using the provided tools. These are YOUR tools - do NOT use system crontab or shell commands for scheduling.\n\n"
            "**CRITICAL: Use ONLY these tools for scheduling:**\n"
            "- `create_cron_task(name, instructions, cron_expression)`: Create a scheduled task\n"
            "- `list_cron_tasks()`: Show all scheduled tasks\n"
            "- `delete_cron_task(task_identifier)`: Remove a task\n\n"
            "**How it works:**\n"
            "When a scheduled task fires, YOU (the agent) will receive the `instructions` as a message and respond to the user. The instructions should describe what YOU should do, not shell commands.\n\n"
            "**Example - User wants a joke every 5 minutes:**\n"
            "```\n"
            "create_cron_task(\n"
            '  name="joke-sender",\n'
            '  instructions="Tell the user a funny joke",\n'
            '  cron_expression="*/5 * * * *"\n'
            ")\n"
            "```\n"
            "**Cron Expression Format (5 fields):**\n"
            "minute hour day month weekday\n"
            "- `*/5 * * * *` = Every 5 minutes\n"
            "- `0 6 * * *` = Daily at 6:00 AM\n"
            "- `0 9 * * 1-5` = Weekdays at 9:00 AM\n"
            "- `0 */2 * * *` = Every 2 hours\n\n"
        )

    def _attachment_context(
        self,
        attachments: list[AttachmentInfo] | None,
        user_id: str,
    ) -> str:
        if not attachments:
            return ""

        workspace_dir = self.working_dir_resolver(user_id)

        lines = ["\n## Received Files\n"]
        for att in attachments:
            file_name = att.file_name or "unknown"
            mime_type = att.mime_type or "unknown"
            file_path = att.file_path or ""
            file_size = att.file_size or 0

            if file_path:
                src_path = Path(file_path)
                if src_path.exists():
                    dest_path = workspace_dir / file_name
                    if src_path.parent != workspace_dir:
                        import shutil

                        shutil.copy2(src_path, dest_path)
                        file_path = str(dest_path)
                        logger.info("Copied attachment %s to workspace: %s", file_name, dest_path)

            if file_size >= 1024 * 1024:
                size_str = f"{file_size / (1024 * 1024):.1f}MB"
            elif file_size >= 1024:
                size_str = f"{file_size / 1024:.1f}KB"
            else:
                size_str = f"{file_size} bytes"

            lines.append(f"- **{file_name}** ({mime_type}, {size_str})")
            lines.append(f"  Path: `{file_path}`")

        lines.append("")
        return "\n".join(lines)

    def _onboarding_section(self, onboarding_context: dict[str, str | None]) -> str:
        """Generate the onboarding section for first-time users.

        When a user first interacts with the agent, this section provides
        the soul.md and id.md content and instructs the agent to welcome the user.

        Args:
            onboarding_context: Dict with 'soul' and 'id' keys containing
                the personality file content.

        Returns:
            Onboarding section string for the prompt.
        """
        soul = onboarding_context.get("soul", "")
        id_content = onboarding_context.get("id", "")

        lines = ["## Welcome - First Interaction\n\n"]
        lines.append(
            "This is the user's first interaction with you! Please send a warm, "
            "friendly welcome message. Introduce yourself and mention that you're here "
            "to help.\n\n"
            "IMPORTANT: Tell the user that you've set up their personalized personality files "
            "(soul.md and id.md) and SHOW them the content of these files below. "
            "This helps them understand your personality and how you'll behave.\n\n"
        )

        if soul:
            lines.append("### Your Personality (soul.md)\n\n")
            lines.append(soul)
            lines.append("\n\n")

        if id_content:
            lines.append("### Your Identity (id.md)\n\n")
            lines.append(id_content)
            lines.append("\n\n")

        return "".join(lines)
