"""Unit tests for StrEnum definitions."""

import pytest

from app.enums import (
    CommandType,
    LogSeverity,
    ModelProvider,
    TaskStatus,
    WebhookEventType,
)


class TestModelProvider:
    """Tests for ModelProvider enum."""

    def test_bedrock_value(self):
        """BEDROCK should have string value 'bedrock'."""
        assert ModelProvider.BEDROCK == "bedrock"
        assert ModelProvider.BEDROCK.value == "bedrock"

    def test_openai_value(self):
        """OPENAI should have string value 'openai'."""
        assert ModelProvider.OPENAI == "openai"
        assert ModelProvider.OPENAI.value == "openai"

    def test_google_value(self):
        """GOOGLE should have string value 'google'."""
        assert ModelProvider.GOOGLE == "google"
        assert ModelProvider.GOOGLE.value == "google"

    def test_all_members(self):
        """ModelProvider should have exactly 3 members."""
        assert len(ModelProvider) == 3
        assert set(ModelProvider) == {
            ModelProvider.BEDROCK,
            ModelProvider.OPENAI,
            ModelProvider.GOOGLE,
        }

    def test_string_comparison(self):
        """Enum values should be comparable to strings."""
        assert ModelProvider.BEDROCK == "bedrock"
        assert "openai" == ModelProvider.OPENAI


class TestTaskStatus:
    """Tests for TaskStatus enum."""

    def test_pending_value(self):
        """PENDING should have string value 'pending'."""
        assert TaskStatus.PENDING == "pending"

    def test_in_progress_value(self):
        """IN_PROGRESS should have string value 'in_progress'."""
        assert TaskStatus.IN_PROGRESS == "in_progress"

    def test_done_value(self):
        """DONE should have string value 'done'."""
        assert TaskStatus.DONE == "done"

    def test_all_members(self):
        """TaskStatus should have exactly 3 members for kanban columns."""
        assert len(TaskStatus) == 3
        assert set(TaskStatus) == {
            TaskStatus.PENDING,
            TaskStatus.IN_PROGRESS,
            TaskStatus.DONE,
        }


class TestCommandType:
    """Tests for CommandType enum."""

    def test_new_value(self):
        """NEW should have string value 'new'."""
        assert CommandType.NEW == "new"

    def test_logs_value(self):
        """LOGS should have string value 'logs'."""
        assert CommandType.LOGS == "logs"

    def test_install_skill_value(self):
        """INSTALL_SKILL should have string value 'install_skill'."""
        assert CommandType.INSTALL_SKILL == "install_skill"

    def test_uninstall_skill_value(self):
        """UNINSTALL_SKILL should have string value 'uninstall_skill'."""
        assert CommandType.UNINSTALL_SKILL == "uninstall_skill"

    def test_help_value(self):
        """HELP should have string value 'help'."""
        assert CommandType.HELP == "help"

    def test_forget_value(self):
        """FORGET should have string value 'forget'."""
        assert CommandType.FORGET == "forget"

    def test_forget_delete_value(self):
        """FORGET_DELETE should have string value 'forget_delete'."""
        assert CommandType.FORGET_DELETE == "forget_delete"

    def test_message_value(self):
        """MESSAGE should have string value 'message'."""
        assert CommandType.MESSAGE == "message"

    def test_all_members(self):
        """CommandType should have exactly 8 members."""
        assert len(CommandType) == 8
        assert set(CommandType) == {
            CommandType.NEW,
            CommandType.LOGS,
            CommandType.INSTALL_SKILL,
            CommandType.UNINSTALL_SKILL,
            CommandType.FORGET,
            CommandType.FORGET_DELETE,
            CommandType.HELP,
            CommandType.MESSAGE,
        }


class TestLogSeverity:
    """Tests for LogSeverity enum."""

    def test_debug_value(self):
        """DEBUG should have string value 'debug'."""
        assert LogSeverity.DEBUG == "debug"

    def test_info_value(self):
        """INFO should have string value 'info'."""
        assert LogSeverity.INFO == "info"

    def test_warning_value(self):
        """WARNING should have string value 'warning'."""
        assert LogSeverity.WARNING == "warning"

    def test_error_value(self):
        """ERROR should have string value 'error'."""
        assert LogSeverity.ERROR == "error"

    def test_all_members(self):
        """LogSeverity should have exactly 4 members."""
        assert len(LogSeverity) == 4


class TestWebhookEventType:
    """Tests for WebhookEventType enum."""

    def test_task_created_value(self):
        """TASK_CREATED should have string value 'task_created'."""
        assert WebhookEventType.TASK_CREATED == "task_created"

    def test_external_trigger_value(self):
        """EXTERNAL_TRIGGER should have string value 'external_trigger'."""
        assert WebhookEventType.EXTERNAL_TRIGGER == "external_trigger"

    def test_all_members(self):
        """WebhookEventType should have exactly 2 members."""
        assert len(WebhookEventType) == 2
