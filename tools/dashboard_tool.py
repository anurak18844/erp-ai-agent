from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient

from config.settings import Settings, get_settings
from tools.mongodb_tool import serialize_bson


OUTSTANDING_STATUSES = ["pending", "partial", "overdue"]
OPEN_MAINTENANCE_STATUSES = ["open", "in_progress"]
OPEN_INCIDENT_STATUSES = ["reported", "investigating"]


class DashboardReader:
    """Small, read-only reporting layer for the internal dashboard."""

    def __init__(self, settings: Settings | None = None, database: Any | None = None):
        self.settings = settings or get_settings()
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
            appname="erp-dashboard-readonly",
        )
        return self._client[self.settings.mongodb_database]

    def read(self) -> dict[str, Any]:
        db = self._get_database()

        payment_summary = self._first(db.payments.aggregate([
            {"$match": {"status": {"$in": OUTSTANDING_STATUSES}}},
            {"$group": {
                "_id": None,
                "count": {"$sum": 1},
                "amount": {"$sum": {"$subtract": ["$amount", "$paid_amount"]}},
            }},
        ]), {"count": 0, "amount": 0})

        summary = {
            "customers": db.customers.count_documents({}),
            "vehicles": db.vehicles.count_documents({}),
            "active_rentals": db.rentals.count_documents({"status": "active"}),
            "outstanding_payments": payment_summary.get("count", 0),
            "outstanding_amount": payment_summary.get("amount", 0),
            "unfinished_maintenance": db.maintenance.count_documents({
                "status": {"$in": OPEN_MAINTENANCE_STATUSES}
            }),
            "open_incidents": db.incidents.count_documents({
                "status": {"$in": OPEN_INCIDENT_STATUSES}
            }),
        }

        vehicle_status = list(db.vehicles.aggregate([
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "_id": 1}},
            {"$project": {"_id": 0, "status": "$_id", "count": 1}},
        ]))

        payment_status = list(db.payments.aggregate([
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "amount": {"$sum": "$amount"},
                "paid_amount": {"$sum": "$paid_amount"},
            }},
            {"$sort": {"count": -1, "_id": 1}},
            {"$project": {
                "_id": 0, "status": "$_id", "count": 1,
                "amount": 1, "paid_amount": 1,
            }},
        ]))

        active_rentals = list(db.rentals.aggregate([
            {"$match": {"status": "active"}},
            {"$lookup": {
                "from": "customers", "localField": "customer_id",
                "foreignField": "_id", "as": "customer",
            }},
            {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
            {"$lookup": {
                "from": "vehicles", "localField": "vehicle_id",
                "foreignField": "_id", "as": "vehicle",
            }},
            {"$unwind": {"path": "$vehicle", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"return_date": 1}},
            {"$limit": 12},
            {"$project": {
                "_id": 0,
                "customer_code": "$customer.customer_code",
                "customer_name": "$customer.full_name",
                "license_plate": "$vehicle.license_plate",
                "vehicle": {"$concat": [
                    {"$ifNull": ["$vehicle.brand", ""]}, " ",
                    {"$ifNull": ["$vehicle.model", ""]},
                ]},
                "start_date": 1,
                "return_date": 1,
            }},
        ]))

        outstanding = list(db.payments.aggregate([
            {"$match": {"status": {"$in": OUTSTANDING_STATUSES}}},
            {"$lookup": {
                "from": "customers", "localField": "customer_id",
                "foreignField": "_id", "as": "customer",
            }},
            {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
            {"$lookup": {
                "from": "rentals", "localField": "rental_id",
                "foreignField": "_id", "as": "rental",
            }},
            {"$unwind": {"path": "$rental", "preserveNullAndEmptyArrays": True}},
            {"$lookup": {
                "from": "vehicles", "localField": "rental.vehicle_id",
                "foreignField": "_id", "as": "vehicle",
            }},
            {"$unwind": {"path": "$vehicle", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"due_date": 1}},
            {"$limit": 12},
            {"$project": {
                "_id": 0,
                "customer_code": "$customer.customer_code",
                "customer_name": "$customer.full_name",
                "license_plate": "$vehicle.license_plate",
                "due_date": 1,
                "status": 1,
                "balance": {"$subtract": ["$amount", "$paid_amount"]},
            }},
        ]))

        maintenance = list(db.maintenance.aggregate([
            {"$match": {"status": {"$in": OPEN_MAINTENANCE_STATUSES}}},
            {"$lookup": {
                "from": "vehicles", "localField": "vehicle_id",
                "foreignField": "_id", "as": "vehicle",
            }},
            {"$unwind": {"path": "$vehicle", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"opened_at": -1}},
            {"$limit": 10},
            {"$project": {
                "_id": 0,
                "license_plate": "$vehicle.license_plate",
                "vehicle": {"$concat": [
                    {"$ifNull": ["$vehicle.brand", ""]}, " ",
                    {"$ifNull": ["$vehicle.model", ""]},
                ]},
                "opened_at": 1,
                "description": 1,
                "status": 1,
            }},
        ]))

        incidents = list(db.incidents.aggregate([
            {"$match": {"status": {"$in": OPEN_INCIDENT_STATUSES}}},
            {"$lookup": {
                "from": "vehicles", "localField": "vehicle_id",
                "foreignField": "_id", "as": "vehicle",
            }},
            {"$unwind": {"path": "$vehicle", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"occurred_at": -1}},
            {"$limit": 8},
            {"$project": {
                "_id": 0,
                "incident_code": 1,
                "license_plate": "$vehicle.license_plate",
                "occurred_at": 1,
                "description": 1,
                "severity": 1,
                "status": 1,
            }},
        ]))

        collection_counts = {
            name: db[name].count_documents({})
            for name in (
                "customers", "vehicles", "rentals", "payments", "maintenance",
                "incidents", "charging_sessions", "installment_plans",
                "installment_schedules", "campaigns", "campaign_engagements",
                "promotions",
            )
        }

        return serialize_bson({
            "generated_at": datetime.now(UTC),
            "database": self.settings.mongodb_database,
            "summary": summary,
            "vehicle_status": vehicle_status,
            "payment_status": payment_status,
            "active_rentals": active_rentals,
            "outstanding_payments": outstanding,
            "maintenance": maintenance,
            "incidents": incidents,
            "collection_counts": collection_counts,
        })

    @staticmethod
    def _first(values: Any, default: dict[str, Any]) -> dict[str, Any]:
        return next(iter(values), default)
