from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from bson import Decimal128, ObjectId
from pymongo import MongoClient
from pymongo.errors import AutoReconnect, NetworkTimeout, PyMongoError, ServerSelectionTimeoutError

from agent.validator import QueryValidator
from config.settings import Settings, get_settings
from models.mongo_query import MongoQuerySpec
from models.mongo_result import MongoResult
from tools.metadata_tool import MetadataCatalog


RETRYABLE_ERRORS = (AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError)


def serialize_bson(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return str(value.to_decimal())
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: serialize_bson(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_bson(child) for child in value]
    return value


class MongoQueryExecutor:
    """The only application component allowed to read MongoDB credentials."""

    def __init__(
        self,
        catalog: MetadataCatalog | None = None,
        settings: Settings | None = None,
        database: Any | None = None,
    ):
        self.catalog = catalog or MetadataCatalog()
        self.settings = settings or get_settings()
        self.validator = QueryValidator(self.catalog, self.settings.max_query_limit)
        self._database = database
        self._client: MongoClient | None = None

    def _get_database(self):
        if self._database is not None:
            return self._database
        if not self.settings.mongodb_uri or not self.settings.mongodb_database:
            raise RuntimeError("MONGODB_URI and MONGODB_DATABASE must be configured")
        self._client = MongoClient(
            self.settings.mongodb_uri,
            serverSelectionTimeoutMS=self.settings.mongo_timeout_ms,
            connectTimeoutMS=self.settings.mongo_timeout_ms,
            socketTimeoutMS=self.settings.mongo_timeout_ms,
            appname="erp-ai-agent-readonly",
        )
        return self._client[self.settings.mongodb_database]

    def execute(self, query: MongoQuerySpec | dict[str, Any]) -> MongoResult:
        started = time.perf_counter()
        validation = self.validator.validate(query)
        if not validation.valid or validation.normalized_query is None:
            return MongoResult(
                success=False,
                error_type="QueryValidationError",
                message="; ".join(validation.errors),
                retryable=False,
                execution_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        spec = validation.normalized_query
        try:
            collection = self._get_database()[spec.collection]
            spec = self._normalize_types(spec)
            data = self._dispatch(collection, spec)
            serialized = serialize_bson(data)
            if not isinstance(serialized, list):
                serialized = [serialized] if serialized is not None else []
            return MongoResult(
                success=True,
                row_count=len(serialized),
                data=serialized,
                execution_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        except Exception as exc:  # convert database failures into the tool contract
            retryable = isinstance(exc, RETRYABLE_ERRORS)
            error_type = type(exc).__name__ if isinstance(exc, (PyMongoError, RuntimeError)) else "MongoQueryError"
            return MongoResult(
                success=False,
                error_type=error_type,
                message=str(exc),
                retryable=retryable,
                execution_ms=round((time.perf_counter() - started) * 1000, 3),
            )

    def _normalize_types(self, spec: MongoQuerySpec) -> MongoQuerySpec:
        metadata = self.catalog.get(spec.collection) or {}
        field_specs = metadata.get("fields", {})

        def resolve_type(
            field_path: str,
            lookup_fields: dict[str, dict[str, Any]],
            base_fields: dict[str, dict[str, Any]],
        ) -> str | None:
            root, separator, remainder = field_path.split(".", 1)[0], "." in field_path, ""
            if separator:
                _, remainder = field_path.split(".", 1)
            if root in lookup_fields and remainder:
                nested_field = remainder.split(".", 1)[0]
                return lookup_fields[root].get(nested_field, {}).get("type")
            return base_fields.get(root, {}).get("type")

        def convert(
            value: Any,
            lookup_fields: dict[str, dict[str, Any]],
            field_path: str | None = None,
            field_type: str | None = None,
            base_fields: dict[str, dict[str, Any]] | None = None,
        ) -> Any:
            base_fields = base_fields if base_fields is not None else field_specs
            if field_type == "ObjectId" and isinstance(value, str) and ObjectId.is_valid(value):
                return ObjectId(value)
            if field_type == "datetime" and isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    return value
            if isinstance(value, dict):
                converted = {}
                for key, child in value.items():
                    if key.startswith("$"):
                        child_path = field_path
                        child_type = field_type
                    else:
                        child_path = f"{field_path}.{key}" if field_path else key
                        child_type = resolve_type(child_path, lookup_fields, base_fields)
                    converted[key] = convert(
                        child, lookup_fields, child_path, child_type, base_fields
                    )
                return converted
            if isinstance(value, list):
                return [
                    convert(child, lookup_fields, field_path, field_type, base_fields)
                    for child in value
                ]
            return value

        def normalize_expressions(value: Any) -> Any:
            if isinstance(value, list):
                return [normalize_expressions(child) for child in value]
            if not isinstance(value, dict):
                return value
            normalized = {
                key: normalize_expressions(child) for key, child in value.items()
            }
            date_expression = normalized.get("$dateFromString")
            if isinstance(date_expression, dict):
                date_string = date_expression.get("dateString")
                if isinstance(date_string, str) and "timezone" in date_expression:
                    try:
                        parsed = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
                    except ValueError:
                        parsed = None
                    if parsed is not None and parsed.tzinfo is not None:
                        # MongoDB forbids specifying `timezone` when dateString
                        # already contains Z or an explicit UTC offset.
                        date_expression = dict(date_expression)
                        date_expression.pop("timezone", None)
                        normalized["$dateFromString"] = date_expression
            return normalized

        def normalize_pipeline(
            stages: list[dict[str, Any]],
            base_fields: dict[str, dict[str, Any]],
        ) -> list[dict[str, Any]]:
            pipeline: list[dict[str, Any]] = []
            lookup_fields: dict[str, dict[str, Any]] = {}
            for original_stage in stages:
                if "$match" in original_stage:
                    stage = {
                        "$match": normalize_expressions(convert(
                            original_stage["$match"], lookup_fields,
                            base_fields=base_fields,
                        ))
                    }
                else:
                    stage = normalize_expressions(original_stage)
                lookup = stage.get("$lookup") if isinstance(stage, dict) else None
                if isinstance(lookup, dict):
                    alias = lookup.get("as")
                    target = lookup.get("from")
                    target_metadata = self.catalog.get(target) if isinstance(target, str) else None
                    if target_metadata and isinstance(lookup.get("pipeline"), list):
                        lookup["pipeline"] = normalize_pipeline(
                            lookup["pipeline"], target_metadata.get("fields", {})
                        )
                    if isinstance(alias, str) and target_metadata:
                        lookup_fields[alias] = target_metadata.get("fields", {})
                pipeline.append(stage)
            return pipeline

        pipeline = normalize_pipeline(list(spec.pipeline or []), field_specs)
        return spec.model_copy(update={
            "filter": convert(spec.filter, {}, base_fields=field_specs),
            "pipeline": pipeline if spec.pipeline is not None else None,
        })

    @staticmethod
    def _dispatch(collection: Any, spec: MongoQuerySpec) -> list[Any]:
        if spec.operation == "find":
            cursor = collection.find(spec.filter, spec.projection)
            if spec.sort:
                cursor = cursor.sort(list(spec.sort.items()))
            return list(cursor.limit(spec.limit or 1))
        if spec.operation == "findOne":
            result = collection.find_one(spec.filter, spec.projection)
            return [] if result is None else [result]
        if spec.operation == "aggregate":
            pipeline = list(spec.pipeline or [])
            if not any("$limit" in stage for stage in pipeline):
                pipeline.append({"$limit": spec.limit or 1})
            return list(collection.aggregate(pipeline, maxTimeMS=5000))
        if spec.operation == "count":
            return [{"count": collection.count_documents(spec.filter, maxTimeMS=5000)}]
        if spec.operation == "distinct":
            values = collection.distinct(spec.distinct_field, spec.filter, maxTimeMS=5000)
            return [{"values": values[: spec.limit or 1]}]
        raise ValueError(f"Unsupported operation: {spec.operation}")


def execute_mongo_query(query_spec: dict[str, Any]) -> dict[str, Any]:
    return MongoQueryExecutor().execute(query_spec).model_dump(mode="json")
