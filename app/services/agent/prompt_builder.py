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


def _redact_yaml_tree(data: Any) -> Any:
    """Recursively replace all leaf values with ``'***'``.

    Preserves dict keys and list structure so the agent can see the
    configuration hierarchy without learning actual secret values.
    """
    if isinstance(data, dict):
        return {k: _redact_yaml_tree(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact_yaml_tree(item) for item in data]
    return "***"


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

        prompt += self._skill_env_vars_section(user_id)
        prompt += self._progress_updates_section()
        prompt += self._shell_timeout_handling_section()

        if self.has_cron:
            prompt += self._scheduling_section()

        prompt += self._security_boundaries_section(user_id)
        prompt += self._browser_credential_section()
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
        lines.append("## Personality (Scratchpad)\n")
        lines.append(
            "The following files are loaded from repo defaults and/or the configured scratchpad and must be followed as system-level instructions.\n"
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
        # Scratchpad access capabilities
        workspace_base = getattr(self.config, "working_folder_base_dir", None)
        out = "\n## Scratchpad Access\n\n"
        if not workspace_base:
            out += (
                "Scratchpad is **not configured** (workspace not set). "
                "You do NOT have filesystem access to the user's scratchpad notes. "
                "Ask the user to paste content or send files as attachments.\n\n"
            )
        else:
            out += (
                "Scratchpad is accessible at `workspace/<USER_ID>/scratchpad/`.\n\n"
                "Constraints and best practices:\n"
                "- The scratchpad lives inside the user's workspace directory.\n"
                "- Use the personality tools to edit `scratchpad/{soul.md,id.md}`.\n"
                "- You may use the injected STM scratchpad (`scratchpad/stm.md`) as context when it appears in this prompt.\n"
            )
        return out

    def _obsidian_stm_section(self, user_id: str) -> str:
        try:
            from app.config import get_user_scratchpad_path
            from app.observability.redaction import redact_text
            from app.tools.short_term_memory_vault import read_raw_text

            scratchpad_dir = str(get_user_scratchpad_path(self.config, user_id, create=False))
            stm_text = read_raw_text(
                scratchpad_dir,
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
                "\n## Short-Term Memory (Scratchpad)\n\n"
                "The following content comes from the user's scratchpad STM note. "
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
            "**⚠️ MANDATORY: Read SKILL.md ONCE Before Using a Skill ⚠️**\n\n"
            "You CANNOT use a skill until you have read its SKILL.md file.\n"
            "Read it ONCE per skill invocation - do NOT re-read the same SKILL.md multiple times.\n"
            "- Do NOT guess the command name from the skill name (e.g., 'tavily_search' skill ≠ 'tavily' CLI)\n"
            "- Do NOT assume a Python venv or CLI exists\n"
            "- Do NOT skip reading SKILL.md - you will likely use the wrong command\n\n"
            "**What NOT to do (these will FAIL):**\n"
            '- ❌ Guessing: `shell(command="tavily search ...")` - WRONG!\n'
            '- ❌ Assuming Python: `source .venv/bin/activate && tavily ...` - WRONG!\n'
            '- ❌ Making up commands based on skill name - WRONG!\n\n'
            "**What TO do (the ONLY correct way):**\n"
            "1. ✅ FIRST: `file_read(path=\"<SKILL_PATH>/SKILL.md\", mode=\"view\")`\n"
            "2. ✅ THEN: Extract the exact bash command pattern from SKILL.md\n"
            "3. ✅ FINALLY: Run the exact command with shell()\n\n"
            "**Example - Using the tavily_search skill:**\n"
            '- Step 1: `file_read(path="/app/skills/splintermaster/tavily-search/SKILL.md", mode="view")`\n'
            '- Step 2: See from SKILL.md: `node ${MORDECAI_SKILLS_BASE_DIR}/[USER]/tavily-search/scripts/search.mjs \"query\"`\n'
            '- Step 3: `shell(command="node /app/skills/splintermaster/tavily-search/scripts/search.mjs \\"NVIDIA stock\\"")`\n\n'
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
                "- Config fields: `set_skill_config(...)` (stored in the database)\n"
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

    def _skill_env_vars_section(self, user_id: str) -> str:
        """List env var names that are pre-loaded from skill secrets.

        This tells the agent which environment variables are already available
        in every shell() call so it can use them directly (e.g. $MY_VAR)
        without asking the user to provide them again.

        Also shows the redacted configuration structure so the agent knows
        the hierarchy of stored settings without seeing actual values.
        """
        try:
            from app.config import get_skill_env_vars

            merged_secrets = self.skill_repo.load_merged_skill_secrets(user_id)
            env_vars = get_skill_env_vars(
                secrets=merged_secrets, skill_name="", user_id=user_id,
            )
        except Exception:
            return ""

        if not env_vars:
            return ""

        lines = ["\n## Pre-loaded Skill Environment Variables\n"]
        lines.append(
            "The following environment variables are automatically injected "
            "into every `shell()` call from skill secrets. "
            "You do NOT need to export or set them — they are already available.\n"
        )
        for name in sorted(env_vars):
            lines.append(f"- `{name}`")
        lines.append("")

        # Show redacted configuration structure (keys visible, values masked).
        try:
            import yaml

            skills_data = merged_secrets.get("skills", {})
            if skills_data and isinstance(skills_data, dict):
                redacted = _redact_yaml_tree(skills_data)
                redacted_yaml = yaml.dump(
                    redacted, default_flow_style=False, sort_keys=True,
                ).strip()
                lines.append("### Configuration Structure (values redacted)\n")
                lines.append("```yaml")
                lines.append(redacted_yaml)
                lines.append("```\n")
        except Exception:
            pass

        return "\n".join(lines)

    def _security_boundaries_section(self, user_id: str) -> str:
        """Instruct the agent to respect per-user data isolation."""
        shared_dir = getattr(self.config, "shared_skills_dir", "skills/shared")
        return (
            "\n## Security Boundaries\n\n"
            "You must NEVER access other users' directories or data.\n"
            f"- Your skill files are in: `skills/{user_id}/`\n"
            f"- You may read shared skills in: `{shared_dir}/`\n"
            "- You must NEVER read, list, or access other users' skill directories\n"
            "- You must NEVER attempt to read skills_secrets.yml files (secrets are managed via the database)\n"
            "- Skill secrets are managed exclusively through `set_skill_env_vars` / `set_skill_config` / `unset_skill_config_keys` tools\n\n"
        )

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

    def _progress_updates_section(self) -> str:
        return (
            "## Progress Updates\n\n"
            "You can send short progress updates to the user during long-running operations. "
            "This helps keep the user informed while you work.\n\n"
            "**When to send progress updates:**\n"
            "- Before starting a long-running operation (e.g., 'Reading large file...')\n"
            "- After completing a significant step (e.g., 'Analysis complete, generating report...')\n"
            "- When operations take more than a few seconds\n\n"
            "**How to use:**\n"
            '- Call `send_progress(message="your status")` with a brief message\n'
            "- Keep messages under 100 characters\n"
            "- Use present continuous tense (e.g., 'Processing data...' not 'Processed data')\n\n"
            "**Examples:**\n"
            '- send_progress(message="Searching for files...")\n'
            '- send_progress(message="Running analysis...")\n'
            '- send_progress(message="Almost done...")\n\n'
            "**Important:**\n"
            "- Don't send progress for instant operations\n"
            "- Don't spam with too many updates (2-3 max per task)\n"
            "- If an error occurs, mention it in your final response\n\n"
        )

    def _shell_timeout_handling_section(self) -> str:
        return (
            "## Shell Command Timeout Handling\n\n"
            "**When a shell command times out (returns `timed_out: True`):**\n\n"
            "1. **Check for partial output**: The `stdout` field may contain useful data that was retrieved before the timeout. Look for `partial_stdout_available: true` or `partial_stdout_length`.\n\n"
            "2. **Use whatever data is available**: Don't discard partial results - use the stdout content to help the user.\n\n"
            "3. **Inform the user**: Explain what happened:\n"
            "   - 'The command timed out after X seconds'\n"
            "   - 'I was able to retrieve Y characters of the transcript/data'\n"
            "   - 'Here's what I found from the partial output...'\n\n"
            "4. **Suggest retry with larger timeout**: If appropriate, tell the user they can retry with `timeout_seconds=600` or higher.\n\n"
            "**Example response:**\n"
            "- 'The transcript fetch timed out after 5 minutes, but I was able to retrieve about 8,000 characters. Here's a summary of what I found...'\n\n"
            "**Example response:**\n"
            "- 'The video is quite long (45 minutes) and the transcript fetch timed out. Try asking again with a note to use a longer timeout.'\n\n"
        )

    def _browser_credential_section(self) -> str:
        """Guidance for step-by-step browser interaction with get_credential."""
        return (
            "\n## Browser + Credential Coordination\n\n"
            "You have a `browser` tool that gives you step-by-step control over a cloud "
            "browser (navigate, fill, click, getText, screenshot, getCookies, setCookies, etc.).\n\n"
            "### Login Flow Pattern\n\n"
            "1. Fetch `username` and `password` via `get_credential(service_name=..., fields='username,password')`.\n"
            "2. Init a browser session: `browser(action={type: 'initSession', session_name: '...'})`.\n"
            "3. Navigate to the login URL (e.g. `https://login.microsoftonline.com`).\n"
            "4. Use `getText` or `screenshot` to observe the page state.\n"
            "5. Fill form fields individually with the `type` action.\n"
            "6. Click submit/next buttons with the `click` action.\n"
            "7. After login, use `getText` to read page content.\n\n"
            "### MFA / OTP Handling\n\n"
            "When a site presents an MFA verification page, there are two patterns:\n\n"
            "**Pattern A — SMS / Text Message verification (interactive):**\n"
            "If the MFA page offers verification methods (e.g. text message, authenticator app), "
            "select the **text message** option and click to send the code. "
            "Then **stop and ask the user** via your response: "
            "'I've requested an SMS verification code to your phone. Please send me the code.' "
            "When the user replies with the code in their next message, resume the browser session "
            "(it stays alive), fill the code field, and click verify.\n\n"
            "**Pattern B — TOTP from 1Password (automatic):**\n"
            "If the service uses a TOTP authenticator and the code is stored in 1Password, "
            "call `get_credential(fields='otp')` for a **fresh** code immediately before filling "
            "the OTP field. OTP codes expire in ~30 seconds. If the site rejects it, fetch a fresh "
            "code and retry up to 2 times.\n\n"
            "**How to decide:** Read the MFA page with `getText`/`screenshot`. If it says "
            "'Text', 'SMS', or 'Send a code to...', use Pattern A. If it shows a TOTP/authenticator "
            "input field directly, use Pattern B.\n\n"
            "### Post-Login Prompts\n\n"
            "After MFA, sites may show a 'Stay signed in?' prompt. "
            "Always check the 'Don't show this again' checkbox and click 'Yes' to "
            "reduce future login prompts and improve cookie persistence.\n\n"
            "### Tips\n\n"
            "- Use `getText` and `screenshot` liberally to understand page state before acting.\n"
            "- Use CSS selectors or text content to identify elements for `click` and `type` actions.\n"
            "- Browser sessions persist between messages — you can start login in one turn and "
            "finish it in the next after the user provides an SMS code.\n"
            "- Cookies persist across sessions via `getCookies`/`setCookies`.\n"
            "- If a session expires, re-init and re-authenticate.\n\n"
        )

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
        # Telegram can send a deterministic onboarding message (including file
        # contents) *before* the user's first message is forwarded to the agent.
        #
        # In that case we pass a sentinel so the agent does not re-send a welcome
        # or repeat the onboarding content.
        if onboarding_context.get("_onboarding_deterministic_sent"):
            return (
                "## Onboarding (Already Sent)\n\n"
                "The system already sent the user the onboarding/personality file contents "
                "as a separate message. Do NOT repeat that content. Do NOT re-introduce yourself.\n\n"
                "Respond directly to the user's message.\n\n"
                "IMPORTANT: Do not end your message with a question. If you need clarification, "
                "ask at most one concise question earlier, and end with a concrete next step.\n\n"
            )

        soul = onboarding_context.get("soul", "")
        id_content = onboarding_context.get("id", "")

        lines = ["## Welcome - First Interaction\n\n"]
        lines.append(
            "This is the user's first interaction with you! Please send a warm, "
            "friendly welcome message. Introduce yourself and mention that you're here "
            "to help.\n\n"
            "IMPORTANT: Do not end your message with a question. End with a short statement "
            "inviting the user to describe what they want to do next.\n\n"
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
