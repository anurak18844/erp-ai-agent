from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta, timezone as fixed_timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.planner import LogicalQueryPlanner
from agent.result_analyzer import ResultAnalyzer
from agent.validator import QueryValidator
from config.settings import Settings, get_settings
from debug.trace_store import TraceStore
from llm.openrouter_client import LLMClient, OpenRouterClient
from models.debug_trace import DebugTrace, RetryAttempt
from models.mongo_result import MongoResult
from tools.metadata_tool import MetadataCatalog
from tools.mongodb_tool import MongoQueryExecutor


class AgentProcessingError(RuntimeError):
    def __init__(self, request_id: str, error_type: str, message: str):
        super().__init__(message)
        self.request_id = request_id
        self.error_type = error_type


class ERPAgentOrchestrator:
    """Bounded, auditable workflow used by FastAPI and by tests.

    The service-layer workflow remains explicit so API responses can include complete traces.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        catalog: MetadataCatalog | None = None,
        executor: MongoQueryExecutor | None = None,
        trace_store: TraceStore | None = None,
    ):
        self.settings = settings or get_settings()
        self.catalog = catalog or MetadataCatalog(self.settings.metadata_dir)
        self.llm = llm or OpenRouterClient(self.settings)
        self.planner = LogicalQueryPlanner(self.llm)
        self.analyzer = ResultAnalyzer(self.llm)
        self.validator = QueryValidator(self.catalog, self.settings.max_query_limit)
        self.executor = executor or MongoQueryExecutor(self.catalog, self.settings)
        self.trace_store = trace_store or TraceStore(self.settings.trace_dir)

    async def run(self, question: str) -> tuple[str, DebugTrace]:
        started = time.perf_counter()
        trace = DebugTrace(request_id=f"req_{uuid.uuid4().hex}", question=question)
        try:
            runtime_context = self._runtime_context()
            trace.runtime_context = runtime_context
            intent = await self.planner.analyze_intent(
                question, sorted(self.catalog.collections)
            )
            trace.intent = intent.model_dump()
            trace.timeline.append("Intent Analysis")
            if not intent.needs_database:
                answer = await self.llm.chat([
                    {"role": "system", "content": "Answer briefly in the user's language."},
                    {"role": "user", "content": question},
                ])
                trace.status, trace.final_answer = "success", answer
                return answer, self._finish(trace, started)

            metadata_query = " ".join([
                question, intent.intent, intent.primary_domain, *intent.secondary_domains
            ])
            search = self.catalog.search(metadata_query)
            trace.metadata_search_query = metadata_query
            trace.metadata = search.model_dump(exclude={"metadata_context"})
            trace.selected_collections = list(search.metadata_context)
            trace.timeline.append("Metadata Search")
            if not search.metadata_context:
                answer = "ไม่พบ metadata ที่เพียงพอสำหรับสร้างคำค้นอย่างปลอดภัย"
                trace.status, trace.final_answer = "insufficient_metadata", answer
                return answer, self._finish(trace, started)

            planned = await self.planner.create_plan(
                question, intent, search.metadata_context, runtime_context
            )
            trace.selected_collections = [
                name for name in planned.plan.collections if name in search.metadata_context
            ]
            trace.query_plan = planned.plan.model_dump()
            trace.mongo_query = planned.query.model_dump(exclude_none=True)
            trace.timeline.append("Query Plan Generated")
            self._describe_selection(trace, planned.plan.required_fields, planned.query, search.metadata_context)

            result = await self._execute_with_repair(
                question, planned.plan, planned.query, search.metadata_context, trace,
                runtime_context,
            )
            trace.execution = result.model_dump(mode="json")
            result_validation = self.analyzer.validate(intent, result)
            trace.result_validation = result_validation
            trace.timeline.append(f"{result.row_count} Record(s) Returned" if result.success else "Query Failed")
            rules = [item["rule"] for item in trace.business_rules]
            answer = await self.analyzer.answer(
                question, intent, result, result_validation, rules, runtime_context
            )
            trace.final_answer = answer
            trace.status = "success" if result.success else "error"
            trace.timeline.append("Final Answer Generated")
            return answer, self._finish(trace, started)
        except Exception as exc:
            trace.status = "error"
            trace.execution = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
            trace.final_answer = "ระบบไม่สามารถประมวลผลคำถามได้ในขณะนี้"
            self._finish(trace, started)
            raise AgentProcessingError(trace.request_id, type(exc).__name__, str(exc)) from exc

    async def _execute_with_repair(
        self, question, plan, query, metadata_context, trace, runtime_context
    ) -> MongoResult:
        current = query
        for attempt in range(1, self.settings.max_agent_retry + 2):
            scope_errors = [
                *self._metadata_scope_errors(current, metadata_context),
                *self._plan_coverage_errors(current, plan),
            ]
            validation = self.validator.validate(current)
            if scope_errors:
                validation.valid = False
                validation.errors = scope_errors + validation.errors
                validation.normalized_query = None
            trace.query_validation = validation.model_dump(mode="json")
            if validation.valid and validation.normalized_query is not None:
                trace.timeline.append("Query Validated")
                current = validation.normalized_query
                result = self.executor.execute(current)
                if result.success:
                    trace.mongo_query = current.model_dump(exclude_none=True)
                    trace.retry_history.append(RetryAttempt(
                        attempt=attempt, status="success", query=current.model_dump(exclude_none=True),
                        row_count=result.row_count,
                    ))
                    trace.timeline.append("Mongo Executed")
                    return result
                error = result.message or result.error_type or "MongoDB execution failed"
            else:
                result = MongoResult(
                    success=False, error_type="QueryValidationError",
                    message="; ".join(validation.errors), retryable=False,
                )
                error = result.message or "Query validation failed"

            trace.retry_history.append(RetryAttempt(
                attempt=attempt, status="error", query=current.model_dump(exclude_none=True), error=error,
            ))
            if attempt > self.settings.max_agent_retry:
                return result
            previous = current.model_dump(exclude_none=True)
            try:
                repaired = await self.planner.repair(
                    question=question, plan=plan, failed_query=current,
                    metadata_context=metadata_context, error=error, attempt=attempt,
                    runtime_context=runtime_context,
                )
            except Exception as repair_error:
                repair_message = (
                    f"Query repair unavailable ({type(repair_error).__name__}): {repair_error}"
                )
                trace.query_repair_summary.append(repair_message)
                trace.retry_history[-1].repair_summary = repair_message
                trace.timeline.append("Query Repair Failed")
                return result
            current = repaired.query
            if current.model_dump(exclude_none=True) == previous:
                trace.query_repair_summary.append("Repair stopped because the query did not change")
                return result
            summary = repaired.repair_summary
            trace.query_repair_summary.append(summary)
            trace.retry_history[-1].repair_summary = summary
            trace.timeline.append(f"Query Repaired (attempt {attempt})")
        return MongoResult(success=False, error_type="RetryLimit", message="Retry limit reached")

    def _runtime_context(self) -> dict[str, str]:
        try:
            timezone = ZoneInfo(self.settings.app_timezone)
        except ZoneInfoNotFoundError:
            if self.settings.app_timezone != "Asia/Bangkok":
                raise
            # Windows may not ship the IANA tz database. Bangkok has no DST,
            # so UTC+07:00 is an exact portable fallback.
            timezone = fixed_timezone(timedelta(hours=7), name="Asia/Bangkok")
        now_local = datetime.now(timezone)
        return {
            "timezone": self.settings.app_timezone,
            "current_datetime_local": now_local.isoformat(),
            "current_date_local": now_local.date().isoformat(),
            "current_datetime_utc": now_local.astimezone(UTC).isoformat(),
            "instruction": "This server clock is the only source of truth for relative dates.",
        }

    @staticmethod
    def _metadata_scope_errors(query, metadata_context: dict[str, Any]) -> list[str]:
        errors = []
        if query.collection not in metadata_context:
            errors.append(f"Collection was not retrieved in metadata context: {query.collection}")
        for stage in query.pipeline or []:
            lookup = stage.get("$lookup") if isinstance(stage, dict) else None
            if lookup and lookup.get("from") not in metadata_context:
                errors.append(f"Lookup collection was not retrieved in metadata context: {lookup.get('from')}")
        return errors

    @staticmethod
    def _plan_coverage_errors(query, plan) -> list[str]:
        """Ensure repair cannot silently replace a required collection with a constant."""
        used_collections = {query.collection}

        def collect(stages) -> None:
            for stage in stages or []:
                if not isinstance(stage, dict):
                    continue
                lookup = stage.get("$lookup")
                if not isinstance(lookup, dict):
                    continue
                target = lookup.get("from")
                if isinstance(target, str):
                    used_collections.add(target)
                collect(lookup.get("pipeline"))

        collect(query.pipeline)
        required_collections = {
            field.split(".", 1)[0]
            for field in plan.required_fields
            if isinstance(field, str) and "." in field
        }
        return [
            f"Query does not read required collection from query plan: {collection}"
            for collection in sorted(required_collections - used_collections)
        ]

    def _describe_selection(self, trace, required_fields, query, context: dict[str, Any]) -> None:
        for field in required_fields:
            collection, _, bare_field = field.partition(".")
            if not bare_field:
                matches = [name for name, doc in context.items() if field in doc["fields"]]
                collection, bare_field = (matches[0], field) if matches else (query.collection, field)
            doc = context.get(collection, {})
            spec = doc.get("fields", {}).get(bare_field, {})
            if spec:
                trace.selected_fields.append({
                    "collection": collection, "field": bare_field,
                    "reason": spec.get("description", "Required by the logical query plan"),
                })
        for stage in query.pipeline or []:
            lookup = stage.get("$lookup")
            if lookup:
                source_doc = context.get(query.collection, {})
                relation = next((rel for rel in source_doc.get("relationships", [])
                                 if rel.get("target_collection") == lookup.get("from")), None)
                if relation is None:
                    target_doc = context.get(lookup.get("from"), {})
                    relation = next(({
                        "target_collection": lookup.get("from"),
                        "local_field": lookup.get("localField"),
                        "foreign_field": lookup.get("foreignField"),
                        "relationship_type": f"reverse of {rel.get('relationship_type', 'declared')}",
                        "description": rel.get("description", "Reverse traversal of declared relationship"),
                    } for rel in target_doc.get("relationships", [])
                        if rel.get("target_collection") == query.collection
                        and rel.get("local_field") == lookup.get("foreignField")
                        and rel.get("foreign_field") == lookup.get("localField")), None)
                if relation:
                    trace.selected_relationships.append(relation)
        for collection in trace.selected_collections:
            for rule in context[collection].get("business_rules", []):
                trace.business_rules.append({"source": self.catalog.source(collection), "rule": rule})
        trace.why_this_query = [
            f"คำถามถูกจัดเป็น intent: {trace.intent.get('intent', '')}",
            f"เลือก collection จาก metadata: {', '.join(trace.selected_collections)}",
            "เลือกเฉพาะ field ที่ Logical Query Plan ต้องใช้",
            "ตรวจ operation, field และ relationship ด้วย metadata ก่อน execute",
        ]

    def _finish(self, trace: DebugTrace, started: float) -> DebugTrace:
        trace.total_execution_ms = round((time.perf_counter() - started) * 1000, 3)
        if self.settings.debug_agent:
            self.trace_store.save(trace)
        return trace
