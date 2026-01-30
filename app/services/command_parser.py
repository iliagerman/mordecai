"""Command parser for user input.

Parses user messages into structured commands using StrEnum types.
Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
"""

from dataclasses import dataclass, field

from app.enums import CommandType


@dataclass
class ParsedCommand:
    """Represents a parsed user command.

    Attributes:
        command_type: The type of command (from CommandType enum)
        args: List of arguments for the command (e.g., URL for install skill)
    """

    command_type: CommandType
    args: list[str] = field(default_factory=list)


class CommandParser:
    """Parses user input into structured commands.

    Supports the following commands:
    - new: Start a new session (Requirement 10.1)
    - logs: Display recent activity logs (Requirement 10.2)
    - install skill <url>: Install a skill from URL (Requirement 10.3)
    - uninstall skill <name>: Uninstall a skill (Requirement 10.4)
    - help: Show available commands (Requirement 10.5)
    - Any other input is treated as a regular message
    """

    # Help text for available commands (Requirement 10.5)
    HELP_TEXT = """Available commands:
- new: Start a new conversation session
- logs: View recent agent activity logs
- install skill <url>: Install a skill from the provided URL
- uninstall skill <name>: Uninstall the specified skill
- help: Show this help message

Any other input will be processed as a regular message to the agent."""

    def parse(self, message: str) -> ParsedCommand:
        """Parse user input into a command.

        Args:
            message: The raw user input string

        Returns:
            ParsedCommand with the identified command type and any arguments
        """
        if not message:
            return ParsedCommand(CommandType.MESSAGE, [])

        message = message.strip()
        lower_message = message.lower()

        # Check for "new" command (Requirement 10.1)
        if lower_message == "new":
            return ParsedCommand(CommandType.NEW)

        # Check for "logs" command (Requirement 10.2)
        if lower_message == "logs":
            return ParsedCommand(CommandType.LOGS)

        # Check for "help" command (Requirement 10.5)
        if lower_message == "help":
            return ParsedCommand(CommandType.HELP)

        # Check for "install skill <url>" command (Requirement 10.3)
        if lower_message.startswith("install skill "):
            # Extract URL preserving case
            url = message[14:].strip()
            if url:
                return ParsedCommand(CommandType.INSTALL_SKILL, [url])
            # Empty URL treated as regular message
            return ParsedCommand(CommandType.MESSAGE, [message])

        # Check for "uninstall skill <name>" command (Requirement 10.4)
        if lower_message.startswith("uninstall skill "):
            # Extract skill name preserving case
            skill_name = message[16:].strip()
            if skill_name:
                return ParsedCommand(CommandType.UNINSTALL_SKILL, [skill_name])
            # Empty skill name treated as regular message
            return ParsedCommand(CommandType.MESSAGE, [message])

        # Default: treat as regular message
        return ParsedCommand(CommandType.MESSAGE, [message])

    def get_help_text(self) -> str:
        """Return the help text for available commands.

        Returns:
            String containing help information for all commands
        """
        return self.HELP_TEXT
