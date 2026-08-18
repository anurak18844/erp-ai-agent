from pathlib import Path

import pytest

from agent.orchestrator import AgentProcessingError, ERPAgentOrchestrator
from agent.planner import PlannedQuery
from agent.planner import RepairedQuery
from debug.trace_store import TraceStore
from models.intent import IntentAnalysis
from models.mongo_query import MongoQuerySpec
from models.mongo_result import MongoResult
from models.query_plan import QueryPlan


class FakeLLM:
    async def generate_structured(self, messages, response_model, temperature=0):
        if response_model is IntentAnalysis:
            return IntentAnalysis(
                intent="find_rental_adjustment_reason", primary_domain="rentals",
                secondary_domains=["promotions"], needs_database=True,
            )
        if response_model is PlannedQuery:
            return PlannedQuery(
                plan=QueryPlan(
                    goal="Find reason for seven additional rental days",
                    collections=["rentals"],
                    required_fields=["rentals.adjustment_days", "rentals.adjustment_reason"],
                    steps=["read adjustment fields", "apply business rule"],
                ),
                query=MongoQuerySpec(
                    operation="findOne", collection="rentals", filter={"adjustment_days": 7},
                    projection={"adjustment_days": 1, "adjustment_reason": 1},
                ),
            )
        raise AssertionError(f"Unexpected structured model: {response_model}")

    async def chat(self, messages, temperature=0.1):
        return "ลูกค้าได้รับวันเช่าเพิ่ม 7 วัน เนื่องจากมีการชดเชยจากปัญหาการใช้งาน"


class FakeExecutor:
    def execute(self, query):
        return MongoResult(success=True, row_count=1, data=[{
            "adjustment_days": 7, "adjustment_reason": "ชดเชยจากปัญหาการใช้งาน",
        }])


@pytest.mark.asyncio
async def test_end_to_end_trace_has_required_decisions(settings, catalog):
    store = TraceStore(settings.trace_dir)
    agent = ERPAgentOrchestrator(
        settings=settings, llm=FakeLLM(), catalog=catalog, executor=FakeExecutor(), trace_store=store,
    )
    answer, trace = await agent.run("ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน")
    assert "7 วัน" in answer
    assert trace.intent
    assert trace.runtime_context["timezone"] == "Asia/Bangkok"
    assert trace.runtime_context["current_date_local"]
    assert "rentals" in trace.selected_collections
    assert {field["field"] for field in trace.selected_fields} >= {"adjustment_days", "adjustment_reason"}
    assert trace.business_rules
    assert trace.mongo_query["operation"] == "findOne"
    assert trace.execution["row_count"] == 1
    assert trace.final_answer == answer
    saved = store.get(trace.request_id)
    assert saved["request_id"] == trace.request_id


def test_trace_store_redacts_secrets(settings):
    from models.debug_trace import DebugTrace
    store = TraceStore(settings.trace_dir)
    trace = DebugTrace(
        request_id="req_secret", question="test",
        execution={"OPENROUTER_API_KEY": "raw-api-value", "message": "mongodb://user:pass@host/db"},
    )
    store.save(trace)
    raw = (Path(settings.trace_dir) / "req_secret.json").read_text(encoding="utf-8")
    assert "raw-api-value" not in raw
    assert "user:pass" not in raw


def test_query_plan_coverage_rejects_placeholder_instead_of_required_lookup():
    plan = QueryPlan(
        goal="Return charging session and its payment status",
        collections=["charging_sessions", "payments"],
        required_fields=["charging_sessions.session_code", "payments.status"],
        steps=["read charging session", "lookup payment"],
    )
    query = MongoQuerySpec(
        operation="aggregate",
        collection="charging_sessions",
        pipeline=[
            {"$match": {"session_code": "CHG-0001"}},
            {"$project": {
                "session_code": 1,
                "payment_status": {"$literal": "no_payment"},
            }},
        ],
    )
    assert ERPAgentOrchestrator._plan_coverage_errors(query, plan) == [
        "Query does not read required collection from query plan: payments"
    ]


@pytest.mark.asyncio
async def test_agent_error_exposes_request_id_and_saves_trace(settings, catalog):
    class FailingLLM:
        async def generate_structured(self, messages, response_model, temperature=0):
            raise RuntimeError("OpenRouter HTTP 404: model not found")

    store = TraceStore(settings.trace_dir)
    agent = ERPAgentOrchestrator(
        settings=settings, llm=FailingLLM(), catalog=catalog,
        executor=FakeExecutor(), trace_store=store,
    )
    with pytest.raises(AgentProcessingError) as raised:
        await agent.run("ทดสอบ error")
    error = raised.value
    assert error.request_id.startswith("req_")
    assert error.error_type == "RuntimeError"
    assert store.get(error.request_id)["status"] == "error"


@pytest.mark.asyncio
async def test_repair_model_failure_does_not_crash_request(settings, catalog):
    class RepairFailingLLM(FakeLLM):
        async def generate_structured(self, messages, response_model, temperature=0):
            if response_model is RepairedQuery:
                raise RuntimeError("invalid structured JSON")
            if response_model is PlannedQuery:
                planned = await super().generate_structured(messages, response_model, temperature)
                planned.query = MongoQuerySpec(
                    operation="find", collection="rentals", filter={"field_that_does_not_exist": 1}
                )
                return planned
            return await super().generate_structured(messages, response_model, temperature)

    store = TraceStore(settings.trace_dir)
    agent = ERPAgentOrchestrator(
        settings=settings, llm=RepairFailingLLM(), catalog=catalog,
        executor=FakeExecutor(), trace_store=store,
    )
    answer, trace = await agent.run("test repair failure")
    assert answer
    assert trace.status == "error"
    assert trace.execution["error_type"] == "QueryValidationError"
    assert "Query Repair Failed" in trace.timeline
