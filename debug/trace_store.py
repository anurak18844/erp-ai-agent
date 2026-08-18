from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models.debug_trace import DebugTrace, Feedback


SENSITIVE_KEY = re.compile(r"(api.?key|mongodb.?uri|password|username|credential|authorization)", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(child) for child in value]
    if isinstance(value, str):
        value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/-]+", "Bearer [REDACTED]", value, flags=re.I)
        value = re.sub(r"mongodb(?:\+srv)?://[^\s]+", "mongodb://[REDACTED]", value, flags=re.I)
    return value


class TraceStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, request_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", request_id):
            raise ValueError("Invalid request_id")
        return self.directory / f"{request_id}.json"

    def save(self, trace: DebugTrace) -> None:
        path = self._path(trace.request_id)
        existing = self.get_document(trace.request_id) if path.exists() else {"feedback": []}
        document = {
            "request_id": trace.request_id,
            "created_at": trace.created_at.isoformat(),
            "question": trace.question,
            "trace": redact(trace.model_dump(mode="json")),
            "feedback": existing.get("feedback", []),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def get_document(self, request_id: str) -> dict[str, Any]:
        path = self._path(request_id)
        if not path.exists():
            raise KeyError(request_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def get(self, request_id: str) -> dict[str, Any]:
        return self.get_document(request_id)["trace"]

    def add_feedback(self, request_id: str, feedback: Feedback) -> dict[str, Any]:
        document = self.get_document(request_id)
        document.setdefault("feedback", []).append(feedback.model_dump(mode="json"))
        self._path(request_id).write_text(
            json.dumps(redact(document), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return document

