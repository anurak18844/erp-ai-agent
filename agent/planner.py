from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.prompts import INTENT_PROMPT, PLAN_PROMPT, REPAIR_PROMPT, SYSTEM_PROMPT
from llm.openrouter_client import LLMClient
from models.intent import IntentAnalysis
from models.mongo_query import MongoQuerySpec
from models.query_plan import QueryPlan


class PlannedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: QueryPlan
    query: MongoQuerySpec


class RepairedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_cause: str
    repair_summary: str
    query: MongoQuerySpec


class LogicalQueryPlanner:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def analyze_intent(
        self, question: str, available_collections: list[str]
    ) -> IntentAnalysis:
        payload = {
            "question": question,
            "available_collections": sorted(available_collections),
        }
        return await self.llm.generate_structured([
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": INTENT_PROMPT + "\n\nInput:\n" + json.dumps(
                    payload, ensure_ascii=False
                ),
            },
        ], IntentAnalysis)

    async def create_plan(
        self, question: str, intent: IntentAnalysis, metadata_context: dict[str, Any],
        runtime_context: dict[str, Any],
    ) -> PlannedQuery:
        payload = {
            "question": question,
            "intent": intent.model_dump(),
            "metadata": metadata_context,
            "runtime_context": runtime_context,
        }
        return await self.llm.generate_structured([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": PLAN_PROMPT + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False)},
        ], PlannedQuery)

    async def repair(
        self,
        *,
        question: str,
        plan: QueryPlan,
        failed_query: MongoQuerySpec,
        metadata_context: dict[str, Any],
        error: str,
        attempt: int,
        runtime_context: dict[str, Any],
    ) -> RepairedQuery:
        payload = {
            "original_question": question,
            "query_plan": plan.model_dump(),
            "query_spec": failed_query.model_dump(exclude_none=True),
            "metadata_context": metadata_context,
            "error": error,
            "attempt": attempt,
            "runtime_context": runtime_context,
        }
        return await self.llm.generate_structured([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": REPAIR_PROMPT + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False)},
        ], RepairedQuery)
