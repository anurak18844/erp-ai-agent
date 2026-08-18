from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MongoQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["find", "findOne", "aggregate", "count", "distinct"]
    collection: str
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, int] | None = None
    pipeline: list[dict[str, Any]] | None = None
    sort: dict[str, int] | None = None
    limit: int | None = None
    distinct_field: str | None = None


class QueryValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    normalized_query: MongoQuerySpec | None = None

