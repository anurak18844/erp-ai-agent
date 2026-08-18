"""Read-only integrity audit for the linked mock ERP dataset."""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from bson.decimal128 import Decimal128
from dotenv import load_dotenv
from pymongo import MongoClient


EXPECTED_COUNTS = {
    "customers": 20,
    "promotions": 4,
    "vehicles": 18,
    "rentals": 48,
    "payments": 48,
    "maintenance": 12,
    "incidents": 8,
    "charging_sessions": 24,
    "installment_plans": 6,
    "installment_schedules": 24,
    "campaigns": 4,
    "campaign_engagements": 24,
}


def main() -> int:
    load_dotenv()
    uri = (os.getenv("MONGODB_SEED_URI", "").strip()
           or os.getenv("MONGODB_URI", "").strip())
    database_name = os.getenv("MONGODB_DATABASE", "").strip()
    if not uri or not database_name:
        raise SystemExit("MONGODB_URI and MONGODB_DATABASE are required")

    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="erp-ai-agent-mock-audit")
    failures: list[str] = []
    try:
        db = client[database_name]
        db.command("ping")
        counts = {name: db[name].count_documents({}) for name in EXPECTED_COUNTS}
        for name, expected in EXPECTED_COUNTS.items():
            if counts[name] != expected:
                failures.append(f"{name}: expected {expected}, found {counts[name]}")

        now = datetime.now(UTC)
        if db.payments.count_documents({"status": "overdue", "due_date": {"$gte": now}}):
            failures.append("overdue payment has a future due_date")
        if db.charging_sessions.count_documents({
            "status": "failed",
            "$or": [
                {"energy_kwh": {"$ne": Decimal128("0.00")}},
                {"cost": {"$ne": Decimal128("0.00")}},
            ],
        }):
            failures.append("failed charging session has non-zero energy or cost")

        active_vehicle_ids = set(db.rentals.distinct("vehicle_id", {"status": "active"}))
        repair_vehicle_ids = set(db.maintenance.distinct(
            "vehicle_id", {"status": {"$in": ["open", "in_progress"]}}
        ))
        if active_vehicle_ids & repair_vehicle_ids:
            failures.append("vehicle is both actively rented and in incomplete maintenance")

        converted = list(db.campaign_engagements.find(
            {"outcome": "converted"}, {"customer_id": 1, "rental_id": 1, "attributed_revenue": 1}
        ))
        rental_customers = {
            item["_id"]: item["customer_id"]
            for item in db.rentals.find(
                {"_id": {"$in": [item.get("rental_id") for item in converted]}},
                {"customer_id": 1},
            )
        }
        if any(
            not item.get("rental_id")
            or rental_customers.get(item["rental_id"]) != item["customer_id"]
            or item["attributed_revenue"].to_decimal() <= 0
            for item in converted
        ):
            failures.append("converted campaign engagement has inconsistent rental/revenue")

        vehicle_models = {
            item["_id"]: item["model"]
            for item in db.vehicles.find({}, {"model": 1})
        }
        model_metrics = defaultdict(lambda: {
            "charging_count": 0,
            "energy_kwh": Decimal("0"),
            "charging_cost": Decimal("0"),
            "incident_count": 0,
            "customer_liability": Decimal("0"),
        })
        for item in db.charging_sessions.find(
            {"status": "completed"}, {"vehicle_id": 1, "energy_kwh": 1, "cost": 1}
        ):
            metric = model_metrics[vehicle_models[item["vehicle_id"]]]
            metric["charging_count"] += 1
            metric["energy_kwh"] += item["energy_kwh"].to_decimal()
            metric["charging_cost"] += item["cost"].to_decimal()
        for item in db.incidents.find({}, {"vehicle_id": 1, "customer_liability": 1}):
            metric = model_metrics[vehicle_models[item["vehicle_id"]]]
            metric["incident_count"] += 1
            metric["customer_liability"] += item["customer_liability"].to_decimal()

        if sum(x["charging_count"] for x in model_metrics.values()) != 20:
            failures.append("completed charging-session total is not 20")
        if sum(x["incident_count"] for x in model_metrics.values()) != 8:
            failures.append("incident total is not 8")
        if sum((x["customer_liability"] for x in model_metrics.values()), Decimal("0")) != Decimal("45750.00"):
            failures.append("customer-liability total is not 45750.00")

        print(f"Database: {database_name}")
        for name, count in counts.items():
            print(f"  {name}: {count}")
        print(f"  total: {sum(counts.values())}")
        print(f"  active-rental vehicles: {len(active_vehicle_ids)}")
        print(f"  incomplete-maintenance vehicles: {len(repair_vehicle_ids)}")
        print(f"  converted campaign engagements: {len(converted)}")
        print("  independent model metrics:")
        for model in sorted(model_metrics):
            metric = model_metrics[model]
            print(
                f"    {model}: charge={metric['charging_count']}, "
                f"kWh={metric['energy_kwh']}, cost={metric['charging_cost']}, "
                f"incidents={metric['incident_count']}, liability={metric['customer_liability']}"
            )
        if failures:
            print("AUDIT FAILED")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print("AUDIT PASSED")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
