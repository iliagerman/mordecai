"""Attachment handling functionality for AgentService.

This module handles file and image attachments including:
- Processing messages with image attachments
- Determining media types from file extensions
- Preparing image content for vision models
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

from app.models.agent import MemoryContext
from app.services.agent.response_extractor import extract_response_text

if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.services.memory_service import MemoryService
    from app.services.agent.state import (
        ConversationHistory as ConversationHistoryState,
        MessageCounter,
    )

logger = logging.getLogger(__name__)


class AttachmentHandler:
    """Handles file and image attachment processing."""

    def __init__(
        self,
        config: AgentConfig,
        memory_service: MemoryService | None,
        conversation_history: ConversationHistoryState,
        message_counter: MessageCounter,
        sync_shared_skills: callable,
        increment_message_count: callable,
        add_to_conversation_history: callable,
        create_model: callable,
        build_system_prompt: callable,
    ):
        """Initialize the attachment handler.

        Args:
            config: Application configuration.
            memory_service: Optional MemoryService.
            conversation_history: Conversation history tracker.
            message_counter: Message counter.
            sync_shared_skills: Function to sync shared skills.
            increment_message_count: Function to increment message count.
            add_to_conversation_history: Function to add to conversation history.
            create_model: Function to create a model (with vision support).
            build_system_prompt: Function to build system prompt.
        """
        self.config = config
        self.memory_service = memory_service
        self._conversation_history = conversation_history
        self._message_counter = message_counter
        self._sync_shared_skills = sync_shared_skills
        self._increment_message_count = increment_message_count
        self._add_to_conversation_history = add_to_conversation_history
        self._create_model = create_model
        self._build_system_prompt = build_system_prompt

    async def process_image_message(
        self,
        user_id: str,
        message: str,
        image_path: str,
    ) -> str:
        """Process a message with an image attachment.

        Attempts to use vision model if configured, falls back to default
        model, and handles errors gracefully by treating image as file.

        The Strands SDK uses the image_reader tool to process images from
        file paths. This method creates an agent with the image_reader tool
        and instructs it to analyze the image at the given path.

        Args:
            user_id: User's telegram ID.
            message: User's text message (caption).
            image_path: Path to the downloaded image file.

        Returns:
            Agent's response text.

        Requirements:
            - 3.1: Use vision model when configured
            - 3.3: Fall back to default model if not configured
            - 3.4: Fall back to file attachment if model doesn't support
            - 3.6: Include caption text with image
            - 8.4: Handle vision processing failures gracefully
        """
        # Keep shared skills mirrored into the user's directory even for
        # image messages (these may still lead to tool usage).
        self._sync_shared_skills(user_id)

        # Increment count for user message
        self._increment_message_count(user_id, 1)

        # Track user message for extraction
        prompt_text = message or f"[Image: {image_path}]"
        self._add_to_conversation_history(user_id, "user", prompt_text)

        # Retrieve memory context
        memory_context: MemoryContext | None = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                memory_context = cast(
                    MemoryContext,
                    self.memory_service.retrieve_memory_context(
                        user_id=user_id, query=message or "image analysis"
                    ),
                )
            except Exception as e:
                logger.warning("Failed to retrieve memory: %s", e)

        try:
            # Try with vision model if configured (Req 3.1, 3.2)
            use_vision = bool(self.config.vision_model_id)
            model = self._create_model(use_vision=use_vision)

            # Use SlidingWindowConversationManager for session memory
            conversation_manager = SlidingWindowConversationManager(
                window_size=self.config.conversation_window_size,
            )

            # Import image_reader tool for vision processing
            try:
                from strands_tools import image_reader

                vision_tools = [image_reader]
            except ImportError:
                logger.warning("image_reader tool not available")
                vision_tools = []

            # Create agent with vision model and image_reader tool
            agent = Agent(
                model=model,
                conversation_manager=conversation_manager,
                tools=vision_tools,
                system_prompt=self._build_system_prompt(user_id, memory_context),
            )

            # Build prompt with image path and caption (Req 3.6)
            if message:
                prompt = f"Please analyze the image at: {image_path}\nUser's message: {message}"
            else:
                prompt = f"Please analyze the image at: {image_path}"

            result = agent(prompt)
            response = self._extract_response_text(result)

        except Exception as e:
            # Fall back to treating as file attachment (Req 3.4, 8.4)
            logger.warning(
                "Vision processing failed for user %s: %s, treating as file attachment", user_id, e
            )
            response = (
                "I received your image but couldn't process it visually. "
                f"The file is saved at: {image_path}\n\n"
                "You can ask me to read or analyze the file using "
                "file system tools."
            )

        # Track agent response for extraction
        self._add_to_conversation_history(user_id, "assistant", response)

        # Increment count for agent response
        self._increment_message_count(user_id, 1)

        return response

    def get_media_type_from_extension(self, file_path: str | Path) -> str:
        """Determine media type from file extension.

        Args:
            file_path: Path to the image file.

        Returns:
            MIME type string for the image.

        Requirements:
            - 3.6: Determine media type from extension for vision model
        """
        ext = Path(file_path).suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        return media_types.get(ext, "image/png")

    def prepare_image_content(
        self,
        image_path: str | Path,
        caption: str | None = None,
    ) -> list[dict]:
        """Prepare image content for vision model input.

        Base64 encodes the image and formats it for the model's expected
        input structure. Includes caption text if provided.

        Args:
            image_path: Path to the image file.
            caption: Optional text caption to include with the image.

        Returns:
            List of content blocks for the model (image + optional text).

        Requirements:
            - 3.6: Include caption text with image in context
        """
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        media_type = self.get_media_type_from_extension(image_path)

        content = []

        # Add image content block
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                },
            }
        )

        # Add caption text if provided (Req 3.6)
        if caption:
            content.append(
                {
                    "type": "text",
                    "text": caption,
                }
            )

        return content

    def _extract_response_text(self, result: Any) -> str:
        """Extract text response from agent result.

        Extracts all text blocks from the agent result and concatenates them,
        filtering out thinking blocks (wrapped in <thinking> tags).

        Args:
            result: Agent result object with message content.

        Returns:
            Concatenated text response without thinking blocks.
        """
        return extract_response_text(result)
