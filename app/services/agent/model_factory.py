from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from strands.models import BedrockModel
from strands.models.gemini import GeminiModel
from strands.models.openai import OpenAIModel

from app.enums import ModelProvider

if TYPE_CHECKING:
    from strands.models.model import Model


@dataclass(slots=True)
class ModelFactory:
    config: Any

    def create(self, *, use_vision: bool = False) -> Model:
        """Create a Strands model instance based on configured provider."""

        # Use vision model if requested and configured
        if use_vision and getattr(self.config, "vision_model_id", None):
            if getattr(self.config, "bedrock_api_key", None):
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = str(self.config.bedrock_api_key)
            return BedrockModel(
                model_id=self.config.vision_model_id,
                region_name=self.config.aws_region,
            )

        match self.config.model_provider:
            case ModelProvider.BEDROCK:
                model_id = self.config.bedrock_model_id
                if getattr(self.config, "bedrock_api_key", None):
                    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = str(self.config.bedrock_api_key)
                return BedrockModel(
                    model_id=model_id,
                    region_name=self.config.aws_region,
                )
            case ModelProvider.OPENAI:
                if not getattr(self.config, "openai_api_key", None):
                    raise ValueError("OpenAI API key required")
                # Some stubs model OpenAIModel as kwargs-only; cast for compatibility.
                return cast(Any, OpenAIModel)(
                    model=self.config.openai_model_id,
                    api_key=self.config.openai_api_key,
                )
            case ModelProvider.GOOGLE:
                if not getattr(self.config, "google_api_key", None):
                    raise ValueError("Google API key required")
                return GeminiModel(
                    client_args={"api_key": self.config.google_api_key},
                    model_id=self.config.google_model_id,
                    params={"max_output_tokens": 4096},
                )
            case _:
                raise ValueError(f"Unknown model provider: {self.config.model_provider}")
