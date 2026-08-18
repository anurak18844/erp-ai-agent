from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.orchestrator import AgentProcessingError, ERPAgentOrchestrator
from config.settings import Settings, get_settings
from debug.trace_store import TraceStore, redact
from models.debug_trace import DebugTrace, Feedback
from tools.dashboard_tool import DashboardReader


logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("erp_ai_agent")
app = FastAPI(title="ERP AI Agent Prototype", version="0.1.0")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    success: bool
    answer: str
    debug: dict[str, Any] | None = None


def get_orchestrator() -> ERPAgentOrchestrator:
    return ERPAgentOrchestrator()


def get_trace_store(settings: Settings = Depends(get_settings)) -> TraceStore:
    return TraceStore(settings.trace_dir)


def get_dashboard_reader(settings: Settings = Depends(get_settings)) -> DashboardReader:
    return DashboardReader(settings)


def _basic_debug(trace: DebugTrace) -> dict[str, Any]:
    return {
        "request_id": trace.request_id,
        "question": trace.question,
        "intent": trace.intent,
        "selected_collections": trace.selected_collections,
        "mongo_query": trace.mongo_query,
        "row_count": trace.execution.get("row_count", 0),
        "final_answer": trace.final_answer,
    }


def _print_answer_to_console(trace: DebugTrace, answer: str) -> None:
    separator = "=" * 72
    print(f"\n{separator}")
    print(f"ERP AI ANSWER  request_id={trace.request_id}")
    print(f"QUESTION       {trace.question}")
    print("-" * 72)
    print(answer)
    print(f"{separator}\n", flush=True)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse("static/dashboard.html")


@app.get("/api/dashboard")
async def dashboard_data(
    reader: DashboardReader = Depends(get_dashboard_reader),
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(reader.read)
    except Exception as exc:
        logger.exception("dashboard_read_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Dashboard data is temporarily unavailable") from exc


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
    orchestrator: ERPAgentOrchestrator = Depends(get_orchestrator),
) -> ChatResponse:
    try:
        answer, trace = await orchestrator.run(request.message)
    except AgentProcessingError as exc:
        safe_message = str(redact(str(exc)))
        logger.exception(
            "agent_request_failed request_id=%s error_type=%s message=%s",
            exc.request_id,
            exc.error_type,
            safe_message,
        )
        detail: dict[str, Any] = {
            "message": "Agent processing failed; inspect server logs or debug trace",
            "request_id": exc.request_id,
            "error_type": exc.error_type,
        }
        if settings.debug_agent:
            detail["error"] = safe_message
            detail["debug_url"] = f"/debug/{exc.request_id}"
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        logger.exception("unexpected_chat_error error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Unexpected server error; inspect server logs") from exc
    logger.info(
        "request_id=%s intent=%s selected_collections=%s operation=%s attempts=%s row_count=%s success=%s",
        trace.request_id,
        trace.intent.get("intent"),
        trace.selected_collections,
        trace.mongo_query.get("operation"),
        len(trace.retry_history),
        trace.execution.get("row_count", 0),
        trace.status == "success",
    )
    if settings.print_answer_to_console:
        _print_answer_to_console(trace, answer)
    debug = None
    if settings.debug_agent:
        debug = trace.model_dump(mode="json") if settings.debug_level == "full" else _basic_debug(trace)
    return ChatResponse(success=trace.status == "success", answer=answer, debug=debug)


@app.get("/debug/{request_id}")
async def get_debug(request_id: str, store: TraceStore = Depends(get_trace_store)) -> dict[str, Any]:
    try:
        return store.get_document(request_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Debug trace not found") from exc


@app.post("/debug/{request_id}/feedback")
async def add_feedback(
    request_id: str, feedback: Feedback, store: TraceStore = Depends(get_trace_store)
) -> dict[str, Any]:
    try:
        document = store.add_feedback(request_id, feedback)
        return {"success": True, "feedback": document["feedback"]}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Debug trace not found") from exc


@app.get("/debug-ui", include_in_schema=False)
async def debug_ui() -> FileResponse:
    return FileResponse("static/debug.html")
