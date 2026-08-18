from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RetryAttempt(BaseModel):
    attempt: int
    status: str
    query: dict[str, Any] | None = None
    error: str | None = None
    repair_summary: str | None = None
    row_count: int | None = None


class DebugTrace(BaseModel):
    request_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    question: str
    status: str = "running"
    runtime_context: dict[str, Any] = Field(default_factory=dict)
    intent: dict[str, Any] = Field(default_factory=dict)
    metadata_search_query: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    selected_collections: list[str] = Field(default_factory=list)
    selected_fields: list[dict[str, Any]] = Field(default_factory=list)
    selected_relationships: list[dict[str, Any]] = Field(default_factory=list)
    business_rules: list[dict[str, Any]] = Field(default_factory=list)
    query_plan: dict[str, Any] = Field(default_factory=dict)
    mongo_query: dict[str, Any] = Field(default_factory=dict)
    query_validation: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    retry_history: list[RetryAttempt] = Field(default_factory=list)
    query_repair_summary: list[str] = Field(default_factory=list)
    result_validation: dict[str, Any] = Field(default_factory=dict)
    why_this_query: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    final_answer: str = ""
    total_execution_ms: float = 0


class Feedback(BaseModel):
    feedback_type: Literal[
        "correct", "wrong_collection", "wrong_field", "wrong_relationship",
        "wrong_business_rule", "wrong_query", "wrong_answer"
    ]
    comment: str = Field(default="", max_length=2000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
