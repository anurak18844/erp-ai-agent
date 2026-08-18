from pydantic import BaseModel, ConfigDict, Field


class IntentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(description="Concise intent name in snake_case")
    primary_domain: str
    secondary_domains: list[str] = Field(default_factory=list)
    needs_database: bool = True

