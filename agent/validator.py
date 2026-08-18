from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from bson import ObjectId

from models.mongo_query import MongoQuerySpec, QueryValidationResult
from tools.metadata_tool import MetadataCatalog


ALLOWED_STAGES = {
    "$match", "$project", "$sort", "$limit", "$skip", "$lookup", "$unwind",
    "$group", "$count", "$addFields",
}
BANNED_TOKENS = {
    "insert", "insertOne", "insertMany", "update", "updateOne", "updateMany",
    "delete", "deleteOne", "deleteMany", "findOneAndUpdate", "findOneAndDelete",
    "bulkWrite", "drop", "renameCollection", "$out", "$merge", "$where", "$function",
}
QUERY_OPERATORS = {
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$exists", "$regex",
    "$options", "$and", "$or", "$nor", "$not", "$elemMatch", "$all", "$size", "$expr",
}
EXPRESSION_OPERATORS = {
    "$sum", "$avg", "$min", "$max", "$first", "$last", "$subtract", "$add", "$cond",
    "$multiply", "$divide", "$round", "$ifNull", "$literal", "$toString", "$dateToString",
    "$size", "$arrayElemAt", "$concat", "$concatArrays", "$filter", "$map", "$reduce",
    "$anyElementTrue", "$allElementsTrue", "$setDifference", "$dateFromString",
    "$and", "$or", "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in",
    "$addToSet", "$push",
}


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


class _PipelineSchema:
    """Fields available at one point in an aggregation pipeline."""

    def __init__(
        self,
        fields: set[str],
        nested: dict[str, set[str]] | None = None,
        origins: dict[str, set[tuple[str, str]]] | None = None,
        nested_origins: dict[str, str] | None = None,
    ):
        self.fields = set(fields)
        self.nested = dict(nested or {})
        self.origins = {key: set(value) for key, value in (origins or {}).items()}
        self.nested_origins = dict(nested_origins or {})

    def has(self, field: str) -> bool:
        path = field.removeprefix("$")
        root, separator, remainder = path.partition(".")
        if root in self.nested:
            if not separator:
                return True
            nested_root = remainder.split(".", 1)[0]
            return nested_root in self.nested[root]
        if root not in self.fields:
            return False
        # Metadata currently describes root fields. Dotted access below a
        # declared root remains allowed for genuine embedded documents.
        return True

    def origins_for(self, field: str) -> set[tuple[str, str]]:
        path = field.removeprefix("$")
        root, separator, remainder = path.partition(".")
        if root in self.nested_origins and separator:
            return {(self.nested_origins[root], remainder)}
        return set(self.origins.get(root, set()))

    def expression_origins(self, value: Any) -> set[tuple[str, str]]:
        origins: set[tuple[str, str]] = set()
        if isinstance(value, str) and value.startswith("$") and not value.startswith("$$"):
            origins.update(self.origins_for(value))
        elif isinstance(value, dict):
            for child in value.values():
                origins.update(self.expression_origins(child))
        elif isinstance(value, list):
            for child in value:
                origins.update(self.expression_origins(child))
        return origins
class QueryValidator:
    def __init__(self, catalog: MetadataCatalog, max_limit: int = 100):
        self.catalog = catalog
        self.max_limit = max_limit

    def validate(self, query: MongoQuerySpec | dict[str, Any]) -> QueryValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            spec = query if isinstance(query, MongoQuerySpec) else MongoQuerySpec.model_validate(query)
        except Exception as exc:
            return QueryValidationResult(valid=False, errors=[f"Invalid query spec: {exc}"])

        for token in _walk(spec.model_dump(exclude_none=True)):
            if isinstance(token, str) and token in BANNED_TOKENS:
                errors.append(f"Forbidden MongoDB token: {token}")

        metadata = self.catalog.get(spec.collection)
        if metadata is None:
            errors.append(f"Unknown collection: {spec.collection}")
            return QueryValidationResult(valid=False, errors=list(dict.fromkeys(errors)))

        fields = set(metadata["fields"])
        self._validate_field_mapping(spec.filter, fields, errors, "filter")
        self._validate_filter_types(spec.filter, metadata["fields"], errors)
        if spec.projection:
            self._validate_field_mapping(spec.projection, fields, errors, "projection")
        if spec.sort:
            self._validate_field_mapping(spec.sort, fields, errors, "sort")
        if spec.distinct_field and self._field_root(spec.distinct_field) not in fields:
            errors.append(f"Unknown field: {spec.distinct_field}")
        if spec.operation == "distinct" and not spec.distinct_field:
            errors.append("distinct_field is required for distinct operation")
        if spec.operation == "aggregate":
            if not spec.pipeline:
                errors.append("pipeline is required for aggregate operation")
            else:
                self._validate_pipeline(spec, metadata, fields, errors, warnings)
        elif spec.pipeline is not None:
            errors.append("pipeline is only allowed for aggregate operation")

        requested_limit = spec.limit if spec.limit is not None else self.max_limit
        if requested_limit < 1:
            errors.append("limit must be greater than zero")
        if requested_limit > self.max_limit:
            warnings.append(f"limit reduced from {requested_limit} to {self.max_limit}")
            spec = spec.model_copy(update={"limit": self.max_limit})
        elif spec.operation in {"find", "aggregate"} and spec.limit is None:
            spec = spec.model_copy(update={"limit": self.max_limit})
        return QueryValidationResult(
            valid=not errors,
            errors=list(dict.fromkeys(errors)),
            warnings=list(dict.fromkeys(warnings)),
            normalized_query=spec if not errors else None,
        )

    @staticmethod
    def _field_root(field: str) -> str:
        return field.removeprefix("$").split(".", 1)[0]

    def _validate_field_mapping(self, value: Any, fields: set[str], errors: list[str], location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.startswith("$"):
                    if key not in QUERY_OPERATORS and key not in EXPRESSION_OPERATORS:
                        errors.append(f"Unsupported operator in {location}: {key}")
                elif self._field_root(key) not in fields:
                    errors.append(f"Unknown field: {key}")
                self._validate_field_mapping(child, fields, errors, location)
        elif isinstance(value, list):
            for item in value:
                self._validate_field_mapping(item, fields, errors, location)

    def _validate_pipeline(self, spec, metadata, fields, errors, warnings) -> _PipelineSchema:
        relationships = metadata.get("relationships", [])
        one_to_many_branches: dict[str, str] = {}
        unwound_one_to_many_branches: set[str] = set()
        schema = _PipelineSchema(
            fields,
            origins={field: {(spec.collection, field)} for field in fields},
        )
        for stage in spec.pipeline or []:
            if not isinstance(stage, dict) or len(stage) != 1:
                errors.append("Each aggregate stage must contain exactly one operator")
                continue
            operator, body = next(iter(stage.items()))
            if operator not in ALLOWED_STAGES:
                errors.append(f"Unsupported aggregate stage: {operator}")
                continue
            if operator == "$match":
                self._validate_pipeline_match(body, schema, errors)
                self._validate_filter_types(body, metadata["fields"], errors)
            elif operator == "$project":
                schema = self._validate_pipeline_project(body, schema, errors)
            elif operator == "$sort":
                if not isinstance(body, dict):
                    errors.append("$sort must be an object")
                    continue
                for field, direction in body.items():
                    if not schema.has(field):
                        errors.append(f"Unknown field: {field}")
                    if direction not in (1, -1):
                        errors.append(f"Invalid sort direction for {field}")
            elif operator == "$lookup":
                if not isinstance(body, dict):
                    errors.append("$lookup must be an object")
                    continue
                target = body.get("from")
                alias = body.get("as")
                target_metadata = self.catalog.get(target) if isinstance(target, str) else None
                if target_metadata is None:
                    errors.append(f"Unknown lookup collection: {target}")
                if not isinstance(alias, str) or not alias or alias.startswith("$") or "." in alias:
                    errors.append("$lookup requires a simple non-empty 'as' field")

                if "pipeline" in body or "let" in body:
                    nested_pipeline = body.get("pipeline")
                    let_bindings = body.get("let", {})
                    local, foreign = body.get("localField"), body.get("foreignField")
                    concise_correlated = False
                    concise_is_one_to_many = False
                    if isinstance(local, str) and isinstance(foreign, str):
                        local_origins = schema.origins_for(local)
                        relationship_match = None
                        for source_collection, source_field in local_origins:
                            source_metadata = self.catalog.get(source_collection) or {}
                            relationship_match = next((
                                rel for rel in source_metadata.get("relationships", [])
                                if rel.get("target_collection") == target
                                and rel.get("local_field") == source_field
                                and rel.get("foreign_field") == foreign
                            ), None)
                            if relationship_match is None:
                                relationship_match = next((
                                    rel for rel in (target_metadata or {}).get("relationships", [])
                                    if rel.get("target_collection") == source_collection
                                    and rel.get("local_field") == foreign
                                    and rel.get("foreign_field") == source_field
                                ), None)
                                if relationship_match is not None:
                                    concise_is_one_to_many = True
                            if relationship_match is not None:
                                break
                        if not schema.has(local):
                            errors.append(f"Unknown lookup local field: {local}")
                        elif target_metadata is not None and foreign not in target_metadata["fields"]:
                            errors.append(f"Unknown lookup foreign field: {target}.{foreign}")
                        elif relationship_match is None:
                            errors.append(
                                f"Unknown relationship for concise pipeline lookup: "
                                f"{local} -> {target}.{foreign}"
                            )
                        else:
                            concise_correlated = True
                    if not isinstance(let_bindings, dict):
                        errors.append("$lookup let must be an object")
                    else:
                        for expression in let_bindings.values():
                            self._validate_expression_fields(expression, schema, errors)
                        correlated = any(
                            isinstance(expression, str)
                            and expression.startswith("$")
                            and not expression.startswith("$$")
                            for expression in let_bindings.values()
                        )
                        if not correlated and not concise_correlated:
                            errors.append(
                                "Pipeline-form $lookup must correlate to an outer-document field; "
                                "constant or empty let bindings create an unsafe Cartesian join"
                            )
                    if not isinstance(nested_pipeline, list) or not nested_pipeline:
                        errors.append("Pipeline-form $lookup requires a non-empty pipeline")
                    elif target_metadata is not None:
                        nested_spec = SimpleNamespace(
                            collection=target,
                            pipeline=nested_pipeline,
                        )
                        nested_schema = self._validate_pipeline(
                            nested_spec,
                            target_metadata,
                            set(target_metadata["fields"]),
                            errors,
                            warnings,
                        )
                    else:
                        nested_schema = None
                    if isinstance(alias, str) and alias and target_metadata is not None:
                        schema.fields.add(alias)
                        schema.nested[alias] = (
                            set(nested_schema.fields)
                            if nested_schema is not None
                            else set(target_metadata["fields"])
                        )
                        schema.nested_origins[alias] = target
                        if concise_is_one_to_many:
                            local_root = self._field_root(local)
                            one_to_many_branches[alias] = one_to_many_branches.get(local_root, alias)
                    continue

                local, foreign = body.get("localField"), body.get("foreignField")
                local_origins = schema.origins_for(local) if isinstance(local, str) else set()
                match = None
                lookup_is_one_to_many = False
                for source_collection, source_field in local_origins:
                    source_metadata = self.catalog.get(source_collection) or {}
                    match = next((
                        rel for rel in source_metadata.get("relationships", [])
                        if rel.get("target_collection") == target
                        and rel.get("local_field") == source_field
                        and rel.get("foreign_field") == foreign
                    ), None)
                    if match is not None:
                        break
                    # MongoDB also permits traversing a declared relationship
                    # in reverse (for example rentals._id -> payments.rental_id).
                    target_relationships = (self.catalog.get(target) or {}).get("relationships", [])
                    match = next((
                        rel for rel in target_relationships
                        if rel.get("target_collection") == source_collection
                        and rel.get("local_field") == foreign
                        and rel.get("foreign_field") == source_field
                    ), None)
                    if match is not None:
                        lookup_is_one_to_many = True
                        break
                if match is None:
                    origin_text = ", ".join(
                        f"{collection}.{field}" for collection, field in sorted(local_origins)
                    ) or f"{spec.collection}.{local}"
                    errors.append(
                        f"Unknown relationship for local field {local} "
                        f"(origin: {origin_text}) -> {target}.{foreign}"
                    )
                if local and not schema.has(local):
                    errors.append(f"Unknown lookup local field: {local}")
                if target_metadata is not None and foreign not in target_metadata["fields"]:
                    errors.append(f"Unknown lookup foreign field: {target}.{foreign}")
                if isinstance(alias, str) and alias and target_metadata is not None:
                    schema.fields.add(alias)
                    schema.nested[alias] = set(target_metadata["fields"])
                    schema.nested_origins[alias] = target
                    if lookup_is_one_to_many:
                        local_root = self._field_root(local)
                        one_to_many_branches[alias] = one_to_many_branches.get(local_root, alias)
            elif operator == "$unwind":
                path = body.get("path") if isinstance(body, dict) else body
                if not isinstance(path, str) or not path.startswith("$"):
                    errors.append("$unwind path must be a field reference")
                elif not schema.has(path):
                    errors.append(f"Unknown unwind field: {path}")
                else:
                    unwind_root = self._field_root(path)
                    if unwind_root in one_to_many_branches:
                        unwound_one_to_many_branches.add(one_to_many_branches[unwind_root])
                    if len(unwound_one_to_many_branches) > 1:
                        errors.append(
                            "Fan-out join risk: multiple independent one-to-many arrays were "
                            "unwound before pre-aggregation: "
                            + ", ".join(sorted(unwound_one_to_many_branches))
                        )
            elif operator in {"$limit", "$skip"}:
                if not isinstance(body, int) or body < 1:
                    errors.append(f"{operator} must be a positive integer")
                elif operator == "$limit" and body > self.max_limit:
                    errors.append(f"$limit exceeds MAX_QUERY_LIMIT ({self.max_limit})")
            elif operator == "$group":
                if not isinstance(body, dict) or "_id" not in body:
                    errors.append("$group must be an object containing _id")
                    continue
                output_origins: dict[str, set[tuple[str, str]]] = {}
                for output_field, expression in body.items():
                    if output_field.startswith("$"):
                        errors.append(f"Invalid $group output field: {output_field}")
                    self._validate_expression_fields(expression, schema, errors)
                    output_origins[output_field] = schema.expression_origins(expression)
                schema = _PipelineSchema(set(body), origins=output_origins)
                one_to_many_branches.clear()
                unwound_one_to_many_branches.clear()
            elif operator == "$addFields":
                if not isinstance(body, dict):
                    errors.append("$addFields must be an object")
                    continue
                for output_field, expression in body.items():
                    if output_field.startswith("$"):
                        errors.append(f"Invalid $addFields output field: {output_field}")
                        continue
                    self._validate_expression_fields(expression, schema, errors)
                    schema.fields.add(self._field_root(output_field))
                    schema.origins[self._field_root(output_field)] = schema.expression_origins(expression)
            elif operator == "$count":
                if not isinstance(body, str) or not body or body.startswith("$"):
                    errors.append("$count must name a non-empty output field")
                else:
                    schema = _PipelineSchema({body})
        return schema

    def _validate_pipeline_match(self, value: Any, schema: _PipelineSchema, errors: list[str]) -> None:
        if not isinstance(value, dict):
            errors.append("$match must be an object")
            return
        for key, child in value.items():
            if key == "$expr":
                self._validate_expression_fields(child, schema, errors)
            elif key.startswith("$"):
                if key not in QUERY_OPERATORS:
                    errors.append(f"Unsupported operator in $match: {key}")
                if isinstance(child, dict):
                    self._validate_pipeline_match(child, schema, errors)
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict):
                            self._validate_pipeline_match(item, schema, errors)
            else:
                if not schema.has(key):
                    errors.append(f"Unknown field: {key}")
                self._validate_condition_operators(child, errors)

    def _validate_condition_operators(self, value: Any, errors: list[str]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.startswith("$") and key not in QUERY_OPERATORS:
                    errors.append(f"Unsupported query operator: {key}")
                self._validate_condition_operators(child, errors)
        elif isinstance(value, list):
            for child in value:
                self._validate_condition_operators(child, errors)

    def _validate_pipeline_project(
        self, body: Any, schema: _PipelineSchema, errors: list[str]
    ) -> _PipelineSchema:
        if not isinstance(body, dict):
            errors.append("$project must be an object")
            return schema
        included: set[str] = set()
        excluded: set[str] = set()
        included_origins: dict[str, set[tuple[str, str]]] = {}
        for output_field, expression in body.items():
            if output_field.startswith("$"):
                errors.append(f"Invalid $project output field: {output_field}")
                continue
            if expression in (0, False):
                excluded.add(self._field_root(output_field))
                continue
            if expression in (1, True):
                if not schema.has(output_field):
                    errors.append(f"Unknown field: {output_field}")
                included_origins[self._field_root(output_field)] = schema.origins_for(output_field)
            else:
                self._validate_expression_fields(expression, schema, errors)
                included_origins[self._field_root(output_field)] = schema.expression_origins(expression)
            included.add(self._field_root(output_field))
        if included:
            return _PipelineSchema(included, origins=included_origins)
        return _PipelineSchema(schema.fields - excluded, {
            key: value for key, value in schema.nested.items() if key not in excluded
        }, {
            key: value for key, value in schema.origins.items() if key not in excluded
        }, {
            key: value for key, value in schema.nested_origins.items() if key not in excluded
        })

    def _validate_expression_fields(self, value: Any, schema: _PipelineSchema, errors: list[str]) -> None:
        if isinstance(value, str) and value.startswith("$") and not value.startswith("$$"):
            if not schema.has(value):
                errors.append(f"Unknown field: {value}")
        elif isinstance(value, dict):
            for key, child in value.items():
                if key.startswith("$") and key not in EXPRESSION_OPERATORS:
                    errors.append(f"Unsupported expression operator: {key}")
                self._validate_expression_fields(child, schema, errors)
        elif isinstance(value, list):
            for child in value:
                self._validate_expression_fields(child, schema, errors)

    def _validate_filter_types(
        self, value: Any, field_specs: dict[str, dict[str, Any]], errors: list[str], current_field: str | None = None
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                field = current_field if key.startswith("$") else self._field_root(key)
                self._validate_filter_types(child, field_specs, errors, field)
            return
        if isinstance(value, list):
            for child in value:
                self._validate_filter_types(child, field_specs, errors, current_field)
            return
        if current_field not in field_specs or value is None:
            return
        expected = field_specs[current_field].get("type")
        valid = True
        if expected == "ObjectId":
            valid = isinstance(value, ObjectId) or (isinstance(value, str) and ObjectId.is_valid(value))
        elif expected == "int":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif expected == "bool":
            valid = isinstance(value, bool)
        elif expected == "string":
            valid = isinstance(value, str)
        elif expected == "datetime":
            if isinstance(value, str):
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    valid = False
            else:
                valid = isinstance(value, datetime)
        elif expected == "decimal":
            valid = isinstance(value, (int, float, str)) and not isinstance(value, bool)
        if not valid:
            errors.append(f"Field {current_field} expects type {expected}")


def validate_query(query: dict[str, Any], catalog: MetadataCatalog | None = None, max_limit: int = 100) -> dict[str, Any]:
    return QueryValidator(catalog or MetadataCatalog(), max_limit).validate(query).model_dump(mode="json")
