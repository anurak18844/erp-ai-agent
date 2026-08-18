import json

import pytest

from agent.result_analyzer import ResultAnalyzer
from models.intent import IntentAnalysis
from models.mongo_result import MongoResult


def test_payment_existence_without_state_is_insufficient():
    result = MongoResult(success=True, row_count=1, data=[{"has_payment": True}])
    validation = ResultAnalyzer.validate(
        IntentAnalysis(
            intent="query_payment_status",
            primary_domain="payments",
            needs_database=True,
        ),
        result,
    )
    assert validation["sufficient"] is False
    assert "cannot establish whether" in validation["reason"]


def test_payment_status_and_amounts_are_sufficient():
    result = MongoResult(success=True, row_count=1, data=[{
        "payment_status": "partial",
        "amount": "4500.00",
        "paid_amount": "2250.00",
        "due_date": "2026-08-14T03:00:00",
    }])
    validation = ResultAnalyzer.validate(
        IntentAnalysis(
            intent="query_payment_status",
            primary_domain="payments",
            needs_database=True,
        ),
        result,
    )
    assert validation["sufficient"] is True


@pytest.mark.asyncio
async def test_technical_query_error_is_not_sent_to_answer_model():
    class CapturingLLM:
        def __init__(self):
            self.messages = None
            self.temperature = None

        async def chat(self, messages, temperature=0.1):
            self.messages = messages
            self.temperature = temperature
            return "ขออภัย ไม่สามารถตรวจสอบข้อมูลนี้ได้ในขณะนี้"

    llm = CapturingLLM()
    analyzer = ResultAnalyzer(llm)
    result = MongoResult(
        success=False,
        error_type="QueryValidationError",
        message="Unsupported query operator: $dateFromString",
    )
    await analyzer.answer(
        "test",
        IntentAnalysis(intent="query", primary_domain="maintenance", needs_database=True),
        result,
        {"sufficient": False, "reason": result.message},
        [],
        {"timezone": "Asia/Bangkok"},
    )
    payload = json.loads(llm.messages[-1]["content"].split("Input:\n", 1)[1])
    serialized = json.dumps(payload)
    assert llm.temperature == 0.0
    assert "$dateFromString" not in serialized
    assert "QueryValidationError" not in serialized
