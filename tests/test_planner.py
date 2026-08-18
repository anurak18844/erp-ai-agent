import json

import pytest

from agent.planner import LogicalQueryPlanner
from models.intent import IntentAnalysis


@pytest.mark.asyncio
async def test_intent_call_receives_only_sorted_collection_names():
    class CapturingLLM:
        def __init__(self):
            self.messages = None

        async def generate_structured(self, messages, response_model, temperature=0):
            self.messages = messages
            assert response_model is IntentAnalysis
            return IntentAnalysis(
                intent="find_unpaid_customers",
                primary_domain="payments",
                secondary_domains=["customers"],
                needs_database=True,
            )

    llm = CapturingLLM()
    planner = LogicalQueryPlanner(llm)
    result = await planner.analyze_intent(
        "ขอลูกค้าที่ยังค้างชำระ",
        ["payments", "customers", "rentals"],
    )

    content = llm.messages[-1]["content"]
    payload = json.loads(content.split("Input:\n", 1)[1])
    assert payload == {
        "question": "ขอลูกค้าที่ยังค้างชำระ",
        "available_collections": ["customers", "payments", "rentals"],
    }
    assert result.primary_domain == "payments"
    assert "description" not in payload
    assert "business_rules" not in payload
