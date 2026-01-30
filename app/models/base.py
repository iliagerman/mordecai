"""JsonModel base class for API communication."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class JsonModel(BaseModel):
    """Base model for API communication with camelCase/snake_case conversion.

    - JSON output uses camelCase (for client communication)
    - Internal Python uses snake_case
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    def model_dump(self, **kwargs) -> dict:
        """Override model_dump - snake_case for internal use."""
        kwargs.setdefault("mode", "json")
        return super().model_dump(**kwargs)

    def model_dump_json(self, **kwargs) -> str:
        """Override to ensure camelCase in JSON output."""
        kwargs.setdefault("by_alias", True)
        return super().model_dump_json(**kwargs)

    def to_json(self, by_alias: bool = True, pretty: bool = False) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(
            indent=2 if pretty else None, exclude_none=True, by_alias=by_alias
        )

    def to_dict(
        self,
        by_alias: bool | None = None,
        include: (
            set[int] | set[str] | dict[int, Any] | dict[str, Any] | None
        ) = None,
        exclude: (
            set[int] | set[str] | dict[int, Any] | dict[str, Any] | None
        ) = None,
        mode: Literal["json", "python", "human"] = "python",
    ) -> dict[str, Any]:
        """Convert to dictionary with flexible options."""
        return self.model_dump(
            exclude_none=True,
            by_alias=by_alias or (mode == "json"),
            include=include,
            exclude=exclude,
            mode=mode,
        )

    @property
    def is_empty(self) -> bool:
        """Check if all fields are empty or None."""
        return not any(
            value
            for field in self.__class__.model_fields
            if (value := getattr(self, field, None)) is not None
            and not (isinstance(value, JsonModel) and value.is_empty)
        )
