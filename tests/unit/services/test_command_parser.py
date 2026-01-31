"""Unit tests for command parser.

Tests command parsing functionality including Property 23.
Requirements: 14.4, 10.1, 10.2, 10.3, 10.4, 10.5
"""

import pytest

from app.enums import CommandType
from app.services.command_parser import CommandParser, ParsedCommand


class TestCommandParser:
    """Unit tests for CommandParser."""

    @pytest.fixture
    def parser(self) -> CommandParser:
        """Create a CommandParser instance."""
        return CommandParser()

    # Basic command tests (Requirements 10.1-10.5)

    def test_parse_new_command(self, parser: CommandParser):
        """Test parsing 'new' command (Requirement 10.1)."""
        result = parser.parse("new")
        assert result.command_type == CommandType.NEW
        assert result.args == []

    def test_parse_new_command_case_insensitive(self, parser: CommandParser):
        """Test 'new' command is case insensitive."""
        for variant in ["NEW", "New", "nEw"]:
            result = parser.parse(variant)
            assert result.command_type == CommandType.NEW

    def test_parse_logs_command(self, parser: CommandParser):
        """Test parsing 'logs' command (Requirement 10.2)."""
        result = parser.parse("logs")
        assert result.command_type == CommandType.LOGS
        assert result.args == []

    def test_parse_logs_command_case_insensitive(self, parser: CommandParser):
        """Test 'logs' command is case insensitive."""
        for variant in ["LOGS", "Logs", "lOgS"]:
            result = parser.parse(variant)
            assert result.command_type == CommandType.LOGS

    def test_parse_help_command(self, parser: CommandParser):
        """Test parsing 'help' command (Requirement 10.5)."""
        result = parser.parse("help")
        assert result.command_type == CommandType.HELP
        assert result.args == []

    def test_parse_help_command_case_insensitive(self, parser: CommandParser):
        """Test 'help' command is case insensitive."""
        for variant in ["HELP", "Help", "hElP"]:
            result = parser.parse(variant)
            assert result.command_type == CommandType.HELP

    def test_parse_install_skill_command(self, parser: CommandParser):
        """Test parsing 'install skill <url>' command (Requirement 10.3)."""
        url = "https://example.com/skill.zip"
        result = parser.parse(f"install skill {url}")
        assert result.command_type == CommandType.INSTALL_SKILL
        assert result.args == [url]

    def test_parse_install_skill_preserves_url_case(self, parser: CommandParser):
        """Test install skill preserves URL case."""
        url = "https://Example.COM/MySkill.zip"
        result = parser.parse(f"install skill {url}")
        assert result.args == [url]

    def test_parse_install_skill_case_insensitive_prefix(self, parser: CommandParser):
        """Test 'install skill' prefix is case insensitive."""
        url = "https://example.com/skill.zip"
        for prefix in ["INSTALL SKILL", "Install Skill", "InStAlL sKiLl"]:
            result = parser.parse(f"{prefix} {url}")
            assert result.command_type == CommandType.INSTALL_SKILL
            assert result.args == [url]

    def test_parse_install_skill_empty_url_is_message(self, parser: CommandParser):
        """Test install skill with empty URL is treated as message."""
        result = parser.parse("install skill ")
        assert result.command_type == CommandType.MESSAGE

    def test_parse_uninstall_skill_command(self, parser: CommandParser):
        """Test parsing 'uninstall skill <name>' command (Requirement 10.4)."""
        skill_name = "my-skill"
        result = parser.parse(f"uninstall skill {skill_name}")
        assert result.command_type == CommandType.UNINSTALL_SKILL
        assert result.args == [skill_name]

    def test_parse_uninstall_skill_preserves_name_case(self, parser: CommandParser):
        """Test uninstall skill preserves skill name case."""
        skill_name = "MySkillName"
        result = parser.parse(f"uninstall skill {skill_name}")
        assert result.args == [skill_name]

    def test_parse_uninstall_skill_case_insensitive_prefix(self, parser: CommandParser):
        """Test 'uninstall skill' prefix is case insensitive."""
        skill_name = "test-skill"
        for prefix in ["UNINSTALL SKILL", "Uninstall Skill", "UnInStAlL sKiLl"]:
            result = parser.parse(f"{prefix} {skill_name}")
            assert result.command_type == CommandType.UNINSTALL_SKILL
            assert result.args == [skill_name]

    def test_parse_uninstall_skill_empty_name_is_message(self, parser: CommandParser):
        """Test uninstall skill with empty name is treated as message."""
        result = parser.parse("uninstall skill ")
        assert result.command_type == CommandType.MESSAGE

    def test_parse_regular_message(self, parser: CommandParser):
        """Test regular messages are parsed as MESSAGE type."""
        message = "Hello, how are you?"
        result = parser.parse(message)
        assert result.command_type == CommandType.MESSAGE
        assert result.args == [message]

    def test_parse_empty_message(self, parser: CommandParser):
        """Test empty message is parsed as MESSAGE type."""
        result = parser.parse("")
        assert result.command_type == CommandType.MESSAGE
        assert result.args == []

    def test_parse_whitespace_only_message(self, parser: CommandParser):
        """Test whitespace-only message is parsed as MESSAGE type."""
        result = parser.parse("   ")
        assert result.command_type == CommandType.MESSAGE
        # After stripping, whitespace becomes empty string which is passed as arg
        assert result.args == [""]

    def test_parse_strips_whitespace(self, parser: CommandParser):
        """Test parser strips leading/trailing whitespace."""
        result = parser.parse("  new  ")
        assert result.command_type == CommandType.NEW

    def test_get_help_text(self, parser: CommandParser):
        """Test help text contains all commands."""
        help_text = parser.get_help_text()
        assert "new" in help_text
        assert "logs" in help_text
        assert "install skill" in help_text
        assert "uninstall skill" in help_text
        assert "help" in help_text
