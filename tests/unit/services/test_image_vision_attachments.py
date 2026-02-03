"""Unit tests for image vision attachment handling.

Tests verify that when an image attachment is processed:
- The agent is created with vision model support
- The image_reader tool is available for the agent to use

This test catches the bug where process_message_with_attachments
was creating a standard agent (not vision-capable) for image attachments.
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider
from app.models.agent import AttachmentInfo
from app.services.agent.agent_creation import AgentCreator


class TestImageVisionAttachments:
    """Tests for image attachment vision handling.

    **Property: Image Attachments Use Vision Model**
    *For any* message with an image attachment (is_image=True),
    processing should create a vision-capable agent with the
    image_reader tool available.

    This regression test catches the bug where process_message_with_attachments
    was creating a standard (non-vision) agent for images, causing the agent
    to try file_read on binary image data and fail.
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration with vision model enabled."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            vision_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            working_folder_base_dir=temp_dir,
            temp_files_base_dir=temp_dir,
            memory_enabled=False,
            personality_enabled=False,
        )

    @pytest.fixture
    def test_image_path(self, temp_dir):
        """Create a minimal test image file."""
        # Create a minimal valid JPEG (just the header + some data)
        image_path = Path(temp_dir) / "test_image.jpg"
        # JPEG magic bytes + minimal payload
        image_path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
        return str(image_path)

    def test_create_agent_with_image_attachment_uses_vision_model(self, config, test_image_path):
        """Test that create_agent with image attachment uses vision model.

        Regression test for bug where create_agent was always calling
        create_model() without use_vision=True, even when processing
        image attachments.

        When is_image=True in attachments:
        1. create_model should be called with use_vision=True
        2. The code should attempt to import and add image_reader tool
        """
        # Create dependencies for AgentCreator
        skill_repo = MagicMock()
        model_factory = MagicMock()
        prompt_builder = MagicMock()
        prompt_builder.build = MagicMock(return_value="Test system prompt")

        # Track calls to create_model
        create_model_calls = []

        def mock_create_model(use_vision=False):
            create_model_calls.append({"use_vision": use_vision})
            mock_model = MagicMock()
            mock_model.provider = "bedrock"
            mock_model.id = "test-model"
            return mock_model

        model_factory.create = mock_create_model

        # Capture log output to verify image_reader import attempt
        with patch("app.services.agent.agent_creation.logger") as mock_logger:
            creator = AgentCreator(
                config=config,
                memory_service=None,
                cron_service=None,
                file_service=None,
                pending_skill_service=None,
                skill_service=None,
                session_manager=MagicMock(),
                skill_repo=skill_repo,
                model_factory=model_factory,
                prompt_builder=prompt_builder,
                user_conversation_managers={},
                user_agents={},
                get_session_id=MagicMock(return_value="test-session"),
                on_agent_name_changed=MagicMock(),
            )

            # Create image attachments
            attachments = [
                AttachmentInfo(
                    file_id="test_file_id",
                    file_name="test_image.jpg",
                    file_path=test_image_path,
                    mime_type="image/jpeg",
                    file_size=1000,
                    is_image=True,
                )
            ]

            # Mock the Agent class
            with patch("strands.Agent") as mock_agent_class:
                mock_agent = MagicMock()
                mock_agent_class.return_value = mock_agent

                # Create agent with image attachment
                creator.create_agent(
                    user_id="test_user",
                    memory_context=None,
                    attachments=attachments,
                )

                # Verify create_model was called with use_vision=True
                assert len(create_model_calls) > 0, "create_model should have been called"
                assert create_model_calls[0]["use_vision"] is True, \
                    f"Expected create_model to be called with use_vision=True for image attachments, got use_vision={create_model_calls[0]['use_vision']}"

                # Verify that a log entry was made about adding image_reader
                # (this confirms the code path was executed)
                log_calls = [str(call) for call in mock_logger.info.call_args_list]
                log_messages = " ".join(log_calls)
                assert "image_reader" in log_messages, \
                    "Expected log message about image_reader tool for image attachments"

    def test_create_agent_without_image_attachment_uses_standard_model(self, config, temp_dir):
        """Test that create_agent without images uses standard model.

        When is_image=False or no image attachments:
        1. create_model should be called with use_vision=False (default)
        """
        # Create a text file
        text_path = Path(temp_dir) / "test_file.txt"
        text_path.write_text("This is text content")

        # Create dependencies for AgentCreator
        skill_repo = MagicMock()
        model_factory = MagicMock()
        prompt_builder = MagicMock()
        prompt_builder.build = MagicMock(return_value="Test system prompt")

        # Track calls to create_model
        create_model_calls = []

        def mock_create_model(use_vision=False):
            create_model_calls.append({"use_vision": use_vision})
            mock_model = MagicMock()
            mock_model.provider = "bedrock"
            mock_model.id = "test-model"
            return mock_model

        model_factory.create = mock_create_model

        creator = AgentCreator(
            config=config,
            memory_service=None,
            cron_service=None,
            file_service=None,
            pending_skill_service=None,
            skill_service=None,
            session_manager=MagicMock(),
            skill_repo=skill_repo,
            model_factory=model_factory,
            prompt_builder=prompt_builder,
            user_conversation_managers={},
            user_agents={},
            get_session_id=MagicMock(return_value="test-session"),
            on_agent_name_changed=MagicMock(),
        )

        # Create non-image attachments
        attachments = [
            AttachmentInfo(
                file_id="test_file_id",
                file_name="test_file.txt",
                file_path=str(text_path),
                mime_type="text/plain",
                file_size=100,
                is_image=False,
            )
        ]

        # Mock the Agent class
        with patch("strands.Agent") as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent

            # Create agent with non-image attachment
            creator.create_agent(
                user_id="test_user",
                memory_context=None,
                attachments=attachments,
            )

            # For non-images, use_vision should be False
            assert len(create_model_calls) > 0, "create_model should have been called"
            assert create_model_calls[0]["use_vision"] is False, \
                f"Expected create_model to be called with use_vision=False for non-image attachments, got use_vision={create_model_calls[0]['use_vision']}"
