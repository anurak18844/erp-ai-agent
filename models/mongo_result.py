from typing import Any

from pydantic import BaseModel, Field


class MongoResult(BaseModel):
    success: bool
    row_count: int = 0
    data: list[Any] = Field(default_factory=list)
    error_type: str | None = None
    message: str | None = None
    retryable: bool = False
    execution_ms: float = 0

