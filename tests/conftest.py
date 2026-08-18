from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from config.settings import Settings
from tools.metadata_tool import MetadataCatalog


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings():
    # Set every field explicitly so a developer's real shell/.env values can
    # never affect unit tests (especially DEBUG_LEVEL and numeric limits).
    # Keep temporary test data inside the project because some Windows
    # environments cannot create pytest's system-level `tmp_path` fixture.
    with TemporaryDirectory(prefix=".pytest-erp-", dir=ROOT) as temporary_dir:
        yield Settings(
            _env_file=None,
            openrouter_api_key="",
            openrouter_model="deepseek/deepseek-v4-flash",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_max_output_tokens=4096,
            app_timezone="Asia/Bangkok",
            mongodb_uri="",
            mongodb_database="",
            metadata_dir=ROOT / "metadata",
            trace_dir=Path(temporary_dir) / "traces",
            max_query_limit=10,
            max_agent_retry=2,
            mongo_timeout_ms=5000,
            debug_agent=True,
            debug_level="full",
            print_answer_to_console=False,
        )


@pytest.fixture
def catalog(settings):
    return MetadataCatalog(settings.metadata_dir)
