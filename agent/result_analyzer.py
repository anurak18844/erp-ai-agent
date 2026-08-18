from __future__ import annotations

import json
from typing import Any

from agent.prompts import ANSWER_PROMPT, SYSTEM_PROMPT
from llm.openrouter_client import LLMClient
from models.intent import IntentAnalysis
from models.mongo_result import MongoResult


class ResultAnalyzer:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    @staticmethod
    def validate(intent: IntentAnalysis, result: MongoResult) -> dict[str, Any]:
        if not result.success:
            return {"sufficient": False, "reason": result.message or "Query failed"}
        if result.row_count == 0:
            return {"sufficient": False, "reason": "No matching ERP records were returned"}
        payment_existence_without_state = any(
            isinstance(row, dict)
            and "has_payment" in row
            and not any(
                key in row
                for key in (
                    "payment_status", "status", "amount", "paid_amount",
                    "outstanding_balance", "balance", "due_date",
                )
            )
            for row in result.data
        )
        if payment_existence_without_state:
            return {
                "sufficient": False,
                "reason": (
                    "Payment document existence was returned without payment status or amounts; "
                    "this cannot establish whether the rental is paid"
                ),
            }
        asks_why = "reason" in intent.intent or "ทำไม" in intent.intent
        if asks_why:
            reason_values = [
                row.get("adjustment_reason") for row in result.data
                if isinstance(row, dict) and "adjustment_reason" in row
            ]
            if reason_values and not any(reason_values):
                return {"sufficient": False, "reason": "The direct reason field is empty"}
        return {"sufficient": True, "reason": "Returned fields can answer the detected intent"}

    async def answer(
        self,
        question: str,
        intent: IntentAnalysis,
        result: MongoResult,
        result_validation: dict[str, Any],
        business_rules: list[str],
        runtime_context: dict[str, Any],
    ) -> str:
        public_result = result.model_dump(mode="json")
        public_validation = dict(result_validation)
        if not result.success:
            # Keep implementation details in the debug trace. The customer-facing
            # model only needs to know that this particular lookup was unavailable.
            public_result["error_type"] = None
            public_result["message"] = "The requested ERP lookup could not be completed."
            public_validation["reason"] = "The requested ERP lookup could not be completed."
        payload = {
            "question": question,
            "intent": intent.model_dump(),
            "result": public_result,
            "result_validation": public_validation,
            "business_rules": business_rules,
            "runtime_context": runtime_context,
        }
        return await self.llm.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ANSWER_PROMPT + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False)},
        ], temperature=0.0)
