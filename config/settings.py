from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v4-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_max_output_tokens: int = Field(default=4096, ge=256, le=32768)
    app_timezone: str = "Asia/Bangkok"
    mongodb_uri: str = ""
    mongodb_database: str = ""
    max_agent_retry: int = Field(default=3, ge=0, le=10)
    max_query_limit: int = Field(default=100, ge=1, le=1000)
    mongo_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    debug_agent: bool = True
    debug_level: Literal["basic", "full"] = "full"
    print_answer_to_console: bool = True
    trace_dir: Path = PROJECT_ROOT / ".debug_traces"
    metadata_dir: Path = PROJECT_ROOT / "metadata"


@lru_cache
def get_settings() -> Settings:
    return Settings()
