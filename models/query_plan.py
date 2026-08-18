from pydantic import BaseModel, ConfigDict, Field


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    collections: list[str] = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
