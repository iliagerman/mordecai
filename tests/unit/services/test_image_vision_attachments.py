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

import asyncio

from app.config import AgentConfig
from app.enums import ModelProvider
from app.models.agent import AttachmentInfo
from app.services.agent.agent_creation import AgentCreator
from app.services.agent.message_processing import MessageProcessor


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

    def test_process_single_image_uses_correct_content_block_format(self, config, test_image_path):
        """Test that _process_single_image uses correct Strands SDK content block format.

        Regression test for bug where ImageContent and ImageSource classes were used
        instead of the correct dictionary format: {"image": {"format": "png", "source": {"bytes": b"..."}}

        The Strands SDK expects image content blocks as dictionaries with the format:
        {"image": {"format": "<png|jpeg|gif|webp>", "source": {"bytes": <bytes>}}}

        Using incorrect format (e.g., ImageContent/ImageSource classes) causes
        "content_type=<format> | unsupported type" errors.
        """
        # Create a MessageProcessor with all required dependencies
        memory_service = MagicMock()
        logging_service = MagicMock()
        conversation_history = {}
        message_counter = MagicMock()
        message_counter.get = MagicMock(return_value=0)  # Return 0 to avoid extraction trigger
        extraction_lock = MagicMock()

        # Track content blocks passed to Agent
        captured_messages = []

        def mock_agent_init(*args, **kwargs):
            # Capture the messages parameter
            messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
            if messages:
                captured_messages.append(messages)
            mock_agent = MagicMock()
            mock_agent.return_value = "Test response"
            return mock_agent

        # Setup mock functions
        def get_session_id(user_id): return f"session-{user_id}"
        def get_user_messages(user_id): return []
        def create_agent(*args, **kwargs):
            mock_agent = MagicMock()
            mock_agent.return_value = "Should not be called"
            return mock_agent
        def create_model(use_vision=False):
            mock_model = MagicMock()
            mock_model.provider = "bedrock"
            mock_model.id = "test-vision-model"
            return mock_model
        def add_to_conversation_history(user_id, role, content): pass
        def sync_shared_skills(user_id): pass
        def increment_message_count(user_id, count): pass
        def maybe_store_explicit_memory(user_id, message): pass
        def trigger_extraction_and_clear(user_id): pass

        extraction_lock.is_locked = MagicMock(return_value=False)

        # Create MessageProcessor
        processor = MessageProcessor(
            config=config,
            memory_service=memory_service,
            logging_service=logging_service,
            conversation_history=conversation_history,
            message_counter=message_counter,
            extraction_lock=extraction_lock,
            get_session_id=get_session_id,
            get_user_messages=get_user_messages,
            create_agent=create_agent,
            create_model=create_model,
            add_to_conversation_history=add_to_conversation_history,
            sync_shared_skills=sync_shared_skills,
            increment_message_count=increment_message_count,
            maybe_store_explicit_memory=maybe_store_explicit_memory,
            trigger_extraction_and_clear=trigger_extraction_and_clear,
        )

        # Mock the Agent class to capture initialization arguments
        with patch("strands.Agent") as mock_agent_class:
            mock_agent_class.side_effect = mock_agent_init

            # Run the async method
            result = asyncio.run(processor._process_single_image(
                user_id="test_user",
                message="What is in this image?",
                image_path=test_image_path,
            ))

        # Verify Agent was called with messages
        assert len(captured_messages) > 0, "Agent should have been initialized with messages"

        # captured_messages is a list of message lists
        messages_list = captured_messages[0]
        assert isinstance(messages_list, list), "Messages should be a list"

        # Get the first message (user message with image)
        first_message = messages_list[0]
        assert isinstance(first_message, dict), "Message should be a dict"
        assert first_message.get("role") == "user", "First message should be user role"

        # Verify content is a list with image block in correct format
        content = first_message.get("content")
        assert isinstance(content, list), "Content should be a list"

        # The image should be in the correct Strands SDK format
        # Format: {"image": {"format": "<png|jpeg|gif|webp>", "source": {"bytes": b"..."}}
        image_blocks = [block for block in content if isinstance(block, dict) and "image" in block]
        assert len(image_blocks) > 0, "Should have at least one image block"

        image_block = image_blocks[0]
        assert "image" in image_block, "Block should have 'image' key"

        image_data = image_block["image"]
        assert isinstance(image_data, dict), "Image data should be a dict"
        assert "format" in image_data, "Image data should have 'format' key"
        assert "source" in image_data, "Image data should have 'source' key"

        # Verify format is a string (jpeg for .jpg files)
        assert image_data["format"] == "jpeg", f"Expected format 'jpeg', got '{image_data['format']}'"

        # Verify source has 'bytes' key
        source = image_data["source"]
        assert isinstance(source, dict), "Source should be a dict"
        assert "bytes" in source, "Source should have 'bytes' key"
        assert isinstance(source["bytes"], bytes), "Bytes should be bytes type"

        # Verify we're NOT using the incorrect ImageContent/ImageSource class format
        # The incorrect format would have 'format' and 'source' directly at block level
        # or use class instances instead of dicts
        for block in content:
            if isinstance(block, dict):
                # Block should not have both 'format' and 'source' at top level
                # (that would be the incorrect ImageContent class format)
                if "format" in block and "source" in block and "image" not in block:
                    raise AssertionError(
                        f"Block has incorrect format: {block}. "
                        "Expected {'image': {'format': ..., 'source': ...}} format"
                    )
