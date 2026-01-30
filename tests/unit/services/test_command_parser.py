"""Unit tests for command parser.

Tests command parsing functionality including Property 23.
Requirements: 14.4, 10.1, 10.2, 10.3, 10.4, 10.5
"""

import pytest
from hypothesis import given, strategies as st, settings

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


class TestCommandParserProperty:
    """Property-based tests for CommandParser.

    **Property 23: Command Parsing and Execution**
    *For any* valid command (new, logs, install skill, uninstall skill),
    the command parser should correctly identify and route it to the
    appropriate handler.
    **Validates: Requirements 11.5, 11.6**
    """

    @pytest.fixture
    def parser(self) -> CommandParser:
        """Create a CommandParser instance."""
        return CommandParser()

    @given(st.sampled_from(["new", "NEW", "New", "nEw", "NeW"]))
    @settings(max_examples=100)
    def test_property_new_command_always_parsed(self, new_variant: str):
        """Property: Any case variant of 'new' parses to NEW command."""
        parser = CommandParser()
        result = parser.parse(new_variant)
        assert result.command_type == CommandType.NEW
        assert result.args == []

    @given(st.sampled_from(["logs", "LOGS", "Logs", "lOgS", "LoGs"]))
    @settings(max_examples=100)
    def test_property_logs_command_always_parsed(self, logs_variant: str):
        """Property: Any case variant of 'logs' parses to LOGS command."""
        parser = CommandParser()
        result = parser.parse(logs_variant)
        assert result.command_type == CommandType.LOGS
        assert result.args == []

    @given(st.sampled_from(["help", "HELP", "Help", "hElP", "HeLp"]))
    @settings(max_examples=100)
    def test_property_help_command_always_parsed(self, help_variant: str):
        """Property: Any case variant of 'help' parses to HELP command."""
        parser = CommandParser()
        result = parser.parse(help_variant)
        assert result.command_type == CommandType.HELP
        assert result.args == []

    @given(
        prefix=st.sampled_from(["install skill", "INSTALL SKILL", "Install Skill"]),
        url=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
            min_size=1,
            max_size=100,
        ).filter(lambda x: x.strip()),
    )
    @settings(max_examples=100)
    def test_property_install_skill_parses_with_url(self, prefix: str, url: str):
        """Property: install skill with non-empty URL parses correctly."""
        parser = CommandParser()
        result = parser.parse(f"{prefix} {url}")
        assert result.command_type == CommandType.INSTALL_SKILL
        assert len(result.args) == 1
        assert result.args[0] == url.strip()

    @given(
        prefix=st.sampled_from(
            ["uninstall skill", "UNINSTALL SKILL", "Uninstall Skill"]
        ),
        skill_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=50,
        ).filter(lambda x: x.strip()),
    )
    @settings(max_examples=100)
    def test_property_uninstall_skill_parses_with_name(
        self, prefix: str, skill_name: str
    ):
        """Property: uninstall skill with non-empty name parses correctly."""
        parser = CommandParser()
        result = parser.parse(f"{prefix} {skill_name}")
        assert result.command_type == CommandType.UNINSTALL_SKILL
        assert len(result.args) == 1
        assert result.args[0] == skill_name.strip()

    @given(
        message=st.text(min_size=1, max_size=200).filter(
            lambda x: x.strip().lower()
            not in ["new", "logs", "help"]
            and not x.strip().lower().startswith("install skill ")
            and not x.strip().lower().startswith("uninstall skill ")
        )
    )
    @settings(max_examples=100)
    def test_property_non_command_is_message(self, message: str):
        """Property: Any non-command text is parsed as MESSAGE."""
        parser = CommandParser()
        result = parser.parse(message)
        assert result.command_type == CommandType.MESSAGE

    @given(
        command_type=st.sampled_from(
            [CommandType.NEW, CommandType.LOGS, CommandType.HELP]
        )
    )
    @settings(max_examples=100)
    def test_property_simple_commands_have_no_args(self, command_type: CommandType):
        """Property: Simple commands (new, logs, help) have empty args."""
        parser = CommandParser()
        result = parser.parse(command_type.value)
        assert result.command_type == command_type
        assert result.args == []

    @given(whitespace=st.text(alphabet=" \t", min_size=0, max_size=10))
    @settings(max_examples=100)
    def test_property_whitespace_padding_ignored(self, whitespace: str):
        """Property: Leading/trailing whitespace doesn't affect parsing."""
        parser = CommandParser()
        result = parser.parse(f"{whitespace}new{whitespace}")
        assert result.command_type == CommandType.NEW
