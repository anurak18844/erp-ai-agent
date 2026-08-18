import pytest
from pydantic import ValidationError

from models.query_plan import QueryPlan


def test_database_query_plan_requires_source_fields():
    with pytest.raises(ValidationError):
        QueryPlan(
            goal="Check whether rental payment is complete",
            collections=["payments"],
            required_fields=[],
            steps=["read payment"],
        )
