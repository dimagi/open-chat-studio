import logging
from typing import TypeAlias

from pydantic import BaseModel, field_serializer
from pydantic_core import PydanticSerializationError

logger = logging.getLogger(__name__)

LoggableType: TypeAlias = str | dict | list | int | float | bool | BaseModel | None


class Log(BaseModel):
    name: str
    message: LoggableType
    type: str

    @field_serializer("message")
    def serialize_message(self, value):
        try:
            if hasattr(value, "model_dump"):
                return value.model_dump()
            return value
        except UnicodeDecodeError:
            return str(value)  # Fallback to string representation
        except PydanticSerializationError:
            return str(value)  # Fallback to string for Pydantic errors
