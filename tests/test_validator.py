import pytest

from agent.validator import QueryValidator


@pytest.fixture
def validator(catalog):
    return QueryValidator(catalog, max_limit=10)


def test_find_passes_and_limit_is_enforced(validator):
    result = validator.validate({
        "operation": "find", "collection": "rentals",
        "filter": {"status": "active"}, "projection": {"return_date": 1}, "limit": 999,
    })
    assert result.valid
    assert result.normalized_query.limit == 10
    assert result.warnings


def test_aggregate_with_declared_relationship_passes(validator):
    result = validator.validate({
        "operation": "aggregate", "collection": "vehicles", "pipeline": [
            {"$match": {"license_plate": "กข1234"}},
        ],
    })
    assert result.valid, result.errors


@pytest.mark.parametrize("query,expected", [
    ({"operation": "find", "collection": "unknown", "filter": {}}, "Unknown collection"),
    ({"operation": "find", "collection": "rentals", "filter": {"rental_reason": "x"}}, "Unknown field"),
    ({"operation": "find", "collection": "rentals", "filter": {"adjustment_days": "seven"}}, "expects type int"),
    ({"operation": "update", "collection": "rentals", "filter": {}}, "Invalid query spec"),
    ({"operation": "delete", "collection": "rentals", "filter": {}}, "Invalid query spec"),
    ({"operation": "find", "collection": "rentals", "filter": {"$where": "x"}}, "$where"),
    ({"operation": "aggregate", "collection": "rentals", "pipeline": [{"$function": {}}]}, "$function"),
    ({"operation": "aggregate", "collection": "rentals", "pipeline": [{"$out": "x"}]}, "$out"),
    ({"operation": "aggregate", "collection": "rentals", "pipeline": [{"$merge": "x"}]}, "$merge"),
])
def test_unsafe_or_unknown_queries_fail(validator, query, expected):
    result = validator.validate(query)
    assert not result.valid
    assert expected in " ".join(result.errors)


def test_undeclared_lookup_relationship_fails(validator):
    result = validator.validate({
        "operation": "aggregate", "collection": "vehicles", "pipeline": [{
            "$lookup": {"from": "customers", "localField": "_id", "foreignField": "_id", "as": "people"}
        }],
    })
    assert not result.valid
    assert "Unknown relationship" in " ".join(result.errors)


def test_nin_is_rejected_as_expression_operator(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [{
            "$match": {"$expr": {"$nin": ["$status", ["paid", "void"]]}}
        }],
    })
    assert not result.valid
    assert "Unsupported expression operator: $nin" in result.errors


def test_pipeline_lookup_rejects_constant_only_cartesian_join(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [{
            "$lookup": {
                "from": "customers",
                "let": {"customer_code": "CUS-0004"},
                "pipeline": [{
                    "$match": {"$expr": {"$eq": ["$customer_code", "$$customer_code"]}}
                }],
                "as": "customer",
            }
        }],
    })
    assert not result.valid
    assert "unsafe Cartesian join" in " ".join(result.errors)


def test_pipeline_lookup_exposes_derived_project_fields_to_outer_pipeline(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "customers",
        "pipeline": [
            {"$lookup": {
                "from": "payments",
                "let": {"customer_id": "$_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$customer_id", "$$customer_id"]}}},
                    {"$project": {
                        "_id": 0,
                        "outstanding": {"$subtract": ["$amount", "$paid_amount"]},
                    }},
                ],
                "as": "outstanding_payments",
            }},
            {"$unwind": "$outstanding_payments"},
            {"$group": {
                "_id": "$_id",
                "total": {"$sum": "$outstanding_payments.outstanding"},
            }},
        ],
    })
    assert result.valid, result.errors


def test_read_only_map_and_reduce_expressions_are_supported(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [{
            "$addFields": {
                "balances": {"$map": {
                    "input": ["$amount", "$paid_amount"],
                    "as": "value",
                    "in": "$$value",
                }},
                "total": {"$reduce": {
                    "input": ["$amount", "$paid_amount"],
                    "initialValue": 0,
                    "in": {"$add": ["$$value", "$$this"]},
                }},
            }
        }],
    })
    assert result.valid, result.errors


def test_read_only_array_boolean_expression_is_supported(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "vehicles",
        "pipeline": [{
            "$addFields": {
                "has_status": {"$anyElementTrue": [{"$map": {
                    "input": ["maintenance", "available"],
                    "as": "value",
                    "in": {"$eq": ["$$value", "$status"]},
                }}]}
            }
        }],
    })
    assert result.valid, result.errors


def test_concise_correlated_lookup_with_filter_pipeline_is_supported(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "vehicles",
        "pipeline": [{
            "$lookup": {
                "from": "charging_sessions",
                "localField": "_id",
                "foreignField": "vehicle_id",
                "pipeline": [{"$match": {"status": "completed"}}],
                "as": "completed_charging",
            }
        }],
    })
    assert result.valid, result.errors


def test_multiple_unwound_reverse_one_to_many_branches_are_rejected(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "vehicles",
        "pipeline": [
            {"$lookup": {
                "from": "charging_sessions", "localField": "_id",
                "foreignField": "vehicle_id", "as": "charging",
            }},
            {"$unwind": "$charging"},
            {"$lookup": {
                "from": "incidents", "localField": "_id",
                "foreignField": "vehicle_id", "as": "incidents",
            }},
            {"$unwind": "$incidents"},
            {"$group": {
                "_id": "$model",
                "charging_cost": {"$sum": "$charging.cost"},
                "incident_cost": {"$sum": "$incidents.estimated_cost"},
            }},
        ],
    })
    assert not result.valid
    assert "Fan-out join risk" in " ".join(result.errors)


def test_dependent_one_to_many_chain_is_not_treated_as_independent_fanout(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "vehicles",
        "pipeline": [
            {"$lookup": {
                "from": "rentals", "localField": "_id",
                "foreignField": "vehicle_id", "as": "rentals",
            }},
            {"$unwind": "$rentals"},
            {"$lookup": {
                "from": "payments", "localField": "rentals._id",
                "foreignField": "rental_id", "as": "payments",
            }},
            {"$unwind": "$payments"},
            {"$group": {
                "_id": "$_id",
                "paid": {"$sum": "$payments.paid_amount"},
            }},
        ],
    })
    assert result.valid, result.errors


@pytest.mark.parametrize("alias", ["customer", "customers", "customer_info"])
def test_lookup_alias_and_derived_aggregate_fields_are_dynamic(validator, alias):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [
            {"$match": {"status": {"$in": ["pending", "partial", "overdue"]}}},
            {"$lookup": {
                "from": "customers",
                "localField": "customer_id",
                "foreignField": "_id",
                "as": alias,
            }},
            {"$unwind": f"${alias}"},
            {"$group": {
                "_id": "$customer_id",
                "customer_code": {"$first": f"${alias}.customer_code"},
                "full_name": {"$first": f"${alias}.full_name"},
                "phone": {"$first": f"${alias}.phone"},
                "customer_status": {"$first": f"${alias}.status"},
                "total_unpaid_balance": {
                    "$sum": {"$subtract": ["$amount", "$paid_amount"]}
                },
                "unpaid_payments_count": {"$sum": 1},
            }},
            {"$project": {
                "_id": 0,
                "customer_id": "$_id",
                "customer_code": 1,
                "full_name": 1,
                "phone": 1,
                "customer_status": 1,
                "total_unpaid_balance": 1,
                "unpaid_payments_count": 1,
            }},
        ],
    })
    assert result.valid, result.errors


def test_unknown_field_inside_lookup_alias_still_fails(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [
            {"$lookup": {
                "from": "customers",
                "localField": "customer_id",
                "foreignField": "_id",
                "as": "anything",
            }},
            {"$project": {"made_up": "$anything.field_that_does_not_exist"}},
        ],
    })
    assert not result.valid
    assert "Unknown field" in " ".join(result.errors)


def test_lookup_uses_field_lineage_after_group(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [
            {"$match": {"status": {"$in": ["pending", "partial", "overdue"]}}},
            {"$group": {"_id": "$customer_id"}},
            {"$lookup": {
                "from": "customers",
                "localField": "_id",
                "foreignField": "_id",
                "as": "any_alias_generated_by_ai",
            }},
            {"$unwind": "$any_alias_generated_by_ai"},
            {"$project": {
                "_id": 0,
                "full_name": "$any_alias_generated_by_ai.full_name",
                "phone": "$any_alias_generated_by_ai.phone",
            }},
        ],
    })
    assert result.valid, result.errors


def test_lookup_accepts_declared_relationship_in_reverse(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "rentals",
        "pipeline": [
            {"$match": {"promotion_id": {"$ne": None}}},
            {"$lookup": {
                "from": "payments",
                "localField": "_id",
                "foreignField": "rental_id",
                "as": "payments_for_rental",
            }},
            {"$project": {
                "_id": 1,
                "payments_for_rental.amount": 1,
                "payments_for_rental.status": 1,
            }},
        ],
    })
    assert result.valid, result.errors


def test_filter_and_date_expressions_are_supported(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "maintenance",
        "pipeline": [
            {"$group": {"_id": "$vehicle_id", "records": {"$first": "$$ROOT"}}},
            {"$addFields": {"recent": {"$filter": {
                "input": ["$records"],
                "as": "m",
                "cond": {"$and": [
                    {"$gte": ["$$m.opened_at", {"$dateFromString": {
                        "dateString": "2026-08-15T15:00:00+07:00",
                        "timezone": "Asia/Bangkok",
                    }}]},
                    {"$lte": ["$$m.opened_at", {"$dateFromString": {
                        "dateString": "2026-08-17T15:00:00+07:00",
                        "timezone": "Asia/Bangkok",
                    }}]},
                ]},
            }}}},
        ],
    })
    assert result.valid, result.errors


def test_read_only_pipeline_lookup_is_supported(validator):
    result = validator.validate({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [
            {"$lookup": {
                "from": "maintenance",
                "let": {"vehicle_id": "$customer_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$vehicle_id", "$$vehicle_id"]}}},
                    {"$match": {"opened_at": {
                        "$gte": "2026-08-15T15:00:00+07:00"
                    }}},
                ],
                "as": "maintenance_records",
            }},
            {"$unwind": "$maintenance_records"},
            {"$project": {"description": "$maintenance_records.description"}},
        ],
    })
    assert result.valid, result.errors
