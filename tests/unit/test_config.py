"""Unit tests for AgentConfig configuration system."""

import json
import os
import tempfile

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider


class TestAgentConfigDefaults:
    """Tests for AgentConfig default values."""

    def test_default_model_provider(self, monkeypatch):
        """Default model provider should be BEDROCK."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig()
        assert config.model_provider == ModelProvider.BEDROCK

    def test_default_bedrock_model_id(self, monkeypatch):
        """Default Bedrock model should be Claude 3 Sonnet."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig()
        assert config.bedrock_model_id == "anthropic.claude-3-sonnet-20240229-v1:0"

    def test_default_openai_model_id(self, monkeypatch):
        """Default OpenAI model should be gpt-4."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig()
        assert config.openai_model_id == "gpt-4"

    def test_default_aws_region(self, monkeypatch):
        """Default AWS region should be us-east-1."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig()
        assert config.aws_region == "us-east-1"

    def test_default_sqs_queue_prefix(self, monkeypatch):
        """Default SQS queue prefix should be 'agent-user-'."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig()
        assert config.sqs_queue_prefix == "agent-user-"

    def test_default_database_url(self, monkeypatch):
        """Default database URL should use aiosqlite."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig()
        assert config.database_url == "sqlite+aiosqlite:///./agent.db"

    def test_default_api_settings(self, monkeypatch):
        """Default API host and port should be 0.0.0.0:8742."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig()
        assert config.api_host == "0.0.0.0"
        assert config.api_port == 8742


class TestAgentConfigRequired:
    """Tests for required configuration fields."""

    def test_telegram_token_required(self):
        """telegram_bot_token is required and should raise without it."""
        with pytest.raises(Exception):
            AgentConfig()

    def test_telegram_token_from_env(self, monkeypatch):
        """telegram_bot_token should be loadable from environment."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "my-bot-token")
        config = AgentConfig()
        assert config.telegram_bot_token == "my-bot-token"


class TestAgentConfigEnvOverrides:
    """Tests for environment variable overrides."""

    def test_model_provider_override(self, monkeypatch):
        """Model provider should be overridable via env var."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("AGENT_MODEL_PROVIDER", "openai")
        config = AgentConfig()
        assert config.model_provider == ModelProvider.OPENAI

    def test_aws_region_override(self, monkeypatch):
        """AWS region should be overridable via env var."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("AGENT_AWS_REGION", "eu-west-1")
        config = AgentConfig()
        assert config.aws_region == "eu-west-1"

    def test_api_port_override(self, monkeypatch):
        """API port should be overridable via env var."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("AGENT_API_PORT", "9000")
        config = AgentConfig()
        assert config.api_port == 9000

    def test_localstack_endpoint_override(self, monkeypatch):
        """LocalStack endpoint should be configurable."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("AGENT_LOCALSTACK_ENDPOINT", "http://localhost:4566")
        config = AgentConfig()
        assert config.localstack_endpoint == "http://localhost:4566"


class TestAgentConfigFromJson:
    """Tests for JSON file loading."""

    def test_from_json_file_with_values(self, monkeypatch):
        """Config should load values from JSON file."""
        # Use .env file approach - set required token via env
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "env-token")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                {
                    "model_provider": "openai",
                    "api_port": 3000,
                    "sqs_queue_prefix": "test-prefix-",
                },
                f,
            )
            f.flush()

            try:
                config = AgentConfig.from_json_file(f.name)
                assert config.model_provider == ModelProvider.OPENAI
                assert config.api_port == 3000
                assert config.sqs_queue_prefix == "test-prefix-"
            finally:
                os.unlink(f.name)

    def test_from_json_file_missing_file(self, monkeypatch):
        """Config should use defaults when JSON file doesn't exist."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
        config = AgentConfig.from_json_file("nonexistent.json")
        assert config.model_provider == ModelProvider.BEDROCK

    def test_json_overrides_defaults(self, monkeypatch):
        """JSON values should override default values."""
        monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "env-token")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"api_port": 9999}, f)
            f.flush()

            try:
                config = AgentConfig.from_json_file(f.name)
                # JSON should override default (8742)
                assert config.api_port == 9999
            finally:
                os.unlink(f.name)
