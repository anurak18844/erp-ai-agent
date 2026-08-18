from datetime import UTC, datetime

import pytest
from bson import ObjectId

from tools.mongodb_tool import MongoQueryExecutor, serialize_bson


class FakeCursor:
    def __init__(self, rows): self.rows = list(rows)
    def sort(self, sort): return self
    def limit(self, limit): return iter(self.rows[:limit])


class FakeCollection:
    def __init__(self, rows=None, error=None): self.rows = rows or []; self.error = error; self.last_query = None
    def _check(self):
        if self.error: raise self.error
    def find(self, query, projection): self._check(); return FakeCursor(self.rows)
    def find_one(self, query, projection): self._check(); self.last_query = query; return self.rows[0] if self.rows else None
    def aggregate(self, pipeline, maxTimeMS): self._check(); self.last_query = pipeline; return iter(self.rows)
    def count_documents(self, query, maxTimeMS): self._check(); return len(self.rows)
    def distinct(self, field, query, maxTimeMS): self._check(); return list(dict.fromkeys(r[field] for r in self.rows))


class FakeDatabase(dict):
    pass


@pytest.mark.parametrize("query,expected", [
    ({"operation": "find", "collection": "rentals", "filter": {}}, 2),
    ({"operation": "findOne", "collection": "rentals", "filter": {}}, 1),
    ({"operation": "aggregate", "collection": "rentals", "pipeline": [{"$match": {"status": "active"}}]}, 2),
    ({"operation": "count", "collection": "rentals", "filter": {}}, 1),
    ({"operation": "distinct", "collection": "rentals", "filter": {}, "distinct_field": "status"}, 1),
])
def test_read_operations(catalog, settings, query, expected):
    db = FakeDatabase(rentals=FakeCollection([{"status": "active"}, {"status": "returned"}]))
    result = MongoQueryExecutor(catalog, settings, db).execute(query)
    assert result.success
    assert result.row_count == expected


def test_objectid_and_datetime_serialization():
    identifier = ObjectId()
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    value = serialize_bson({"_id": identifier, "when": moment})
    assert value == {"_id": str(identifier), "when": moment.isoformat()}


def test_objectid_filter_is_converted_from_structured_json(catalog, settings):
    identifier = ObjectId()
    collection = FakeCollection([{"_id": identifier}])
    db = FakeDatabase(rentals=collection)
    result = MongoQueryExecutor(catalog, settings, db).execute({
        "operation": "findOne", "collection": "rentals", "filter": {"customer_id": str(identifier)},
    })
    assert result.success
    assert collection.last_query["customer_id"] == identifier


def test_limit(catalog, settings):
    db = FakeDatabase(rentals=FakeCollection([{"status": "active"}] * 20))
    result = MongoQueryExecutor(catalog, settings, db).execute({
        "operation": "find", "collection": "rentals", "filter": {}, "limit": 50,
    })
    assert result.success and result.row_count == settings.max_query_limit


def test_error_handling(catalog, settings):
    db = FakeDatabase(rentals=FakeCollection(error=RuntimeError("database unavailable")))
    result = MongoQueryExecutor(catalog, settings, db).execute({
        "operation": "find", "collection": "rentals", "filter": {},
    })
    assert not result.success
    assert result.error_type == "RuntimeError"


def test_datetime_inside_lookup_alias_is_converted(catalog, settings):
    collection = FakeCollection([])
    db = FakeDatabase(payments=collection)
    result = MongoQueryExecutor(catalog, settings, db).execute({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [
            {"$lookup": {
                "from": "rentals", "localField": "rental_id",
                "foreignField": "_id", "as": "rental",
            }},
            {"$unwind": "$rental"},
            {"$lookup": {
                "from": "vehicles", "localField": "rental.vehicle_id",
                "foreignField": "_id", "as": "vehicle",
            }},
            {"$unwind": "$vehicle"},
            {"$lookup": {
                "from": "maintenance", "localField": "vehicle._id",
                "foreignField": "vehicle_id", "as": "maintenance_records",
            }},
            {"$match": {"maintenance_records": {"$elemMatch": {
                "opened_at": {"$gte": "2026-08-15T00:00:00+07:00"}
            }}}},
        ],
    })
    assert result.success
    date_value = collection.last_query[5]["$match"]["maintenance_records"]["$elemMatch"]["opened_at"]["$gte"]
    assert isinstance(date_value, datetime)
    assert date_value.utcoffset().total_seconds() == 7 * 60 * 60


def test_redundant_timezone_is_removed_from_date_from_string(catalog, settings):
    collection = FakeCollection([])
    db = FakeDatabase(maintenance=collection)
    result = MongoQueryExecutor(catalog, settings, db).execute({
        "operation": "aggregate",
        "collection": "maintenance",
        "pipeline": [{"$addFields": {"boundary": {"$dateFromString": {
            "dateString": "2026-08-15T15:00:00+07:00",
            "timezone": "Asia/Bangkok",
        }}}}],
    })
    assert result.success
    expression = collection.last_query[0]["$addFields"]["boundary"]["$dateFromString"]
    assert expression["dateString"].endswith("+07:00")
    assert "timezone" not in expression


def test_datetime_inside_pipeline_lookup_is_converted(catalog, settings):
    collection = FakeCollection([])
    db = FakeDatabase(payments=collection)
    result = MongoQueryExecutor(catalog, settings, db).execute({
        "operation": "aggregate",
        "collection": "payments",
        "pipeline": [{"$lookup": {
            "from": "maintenance",
            "let": {"vehicle_id": "$customer_id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$vehicle_id", "$$vehicle_id"]}}},
                {"$match": {"opened_at": {
                    "$gte": "2026-08-15T15:00:00+07:00"
                }}},
            ],
            "as": "maintenance_records",
        }}],
    })
    assert result.success
    date_value = collection.last_query[0]["$lookup"]["pipeline"][1]["$match"]["opened_at"]["$gte"]
    assert isinstance(date_value, datetime)
