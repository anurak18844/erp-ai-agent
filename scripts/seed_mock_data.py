"""Create deterministic, linked mock ERP data in MongoDB.

This script writes only to MONGODB_DATABASE, uses MONGODB_SEED_URI when set,
otherwise falls back to MONGODB_URI, and never deletes existing records.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bson import Decimal128, ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient, ReplaceOne


MOCK_NAMESPACE = "erp-ai-agent-demo-v1"
THAI_FIRST_NAMES = [
    "กิตติ", "ชลธิชา", "ณัฐวุฒิ", "ปวีณา", "ธนกร", "วราภรณ์", "ศุภชัย",
    "พิมพ์ชนก", "ภัทร", "สุภาวดี", "อาทิตย์", "มนัสวี", "ธีรภัทร", "รัตนา",
    "ก้องภพ", "นภัสสร", "จักรกฤษณ์", "เบญจมาศ", "พิชญะ", "ศิริพร",
]
THAI_LAST_NAMES = [
    "สุขใจ", "เจริญกิจ", "ตั้งมั่น", "วงศ์ดี", "แสงทอง", "ศรีวัฒนา",
    "รุ่งเรือง", "ธรรมรักษ์", "กุลประเสริฐ", "ชัยมงคล",
]
VEHICLE_MODELS = [
    ("BYD", "Atto 3"), ("MG", "MG4 Electric"), ("Tesla", "Model 3"),
    ("Neta", "V-II"), ("ORA", "Good Cat"), ("Aion", "Y Plus"),
]
PROVINCE_LETTERS = ["กข", "ขค", "งจ", "ชฎ", "ธน", "รย"]


def stable_id(kind: str, index: int) -> ObjectId:
    digest = hashlib.sha256(f"{MOCK_NAMESPACE}:{kind}:{index}".encode()).digest()
    return ObjectId(digest[:12])


def money(value: int | str | Decimal) -> Decimal128:
    return Decimal128(Decimal(str(value)).quantize(Decimal("0.01")))


def build_customers(count: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "_id": stable_id("customer", i),
            "customer_code": f"CUS-{i + 1:04d}",
            "full_name": f"{THAI_FIRST_NAMES[i % len(THAI_FIRST_NAMES)]} {THAI_LAST_NAMES[(i * 3) % len(THAI_LAST_NAMES)]}",
            "phone": f"08{(10000000 + i * 7919) % 100000000:08d}",
            "status": "blocked" if i == count - 1 else "active",
        }
        for i in range(count)
    ]


def build_promotions() -> list[dict[str, Any]]:
    definitions = [
        ("WELCOME3", "ลูกค้าใหม่เพิ่ม 3 วัน", "สมัครผ่านแอป", 3, True),
        ("CORP5", "สิทธิ์พนักงานองค์กร", "โครงการพันธมิตรองค์กร", 5, True),
        ("EVCLUB2", "สมาชิก EV Club", "สมาชิกสะสมคะแนน", 2, True),
        ("OLD2025", "แคมเปญปี 2025", "งานมหกรรมรถไฟฟ้า", 1, False),
    ]
    return [
        {
            "_id": stable_id("promotion", i), "code": code, "name": name,
            "source": source, "extra_days": days, "active": active,
        }
        for i, (code, name, source, days, active) in enumerate(definitions)
    ]


def build_vehicles(count: int = 18) -> list[dict[str, Any]]:
    vehicles = []
    for i in range(count):
        brand, model = VEHICLE_MODELS[i % len(VEHICLE_MODELS)]
        vehicles.append({
            "_id": stable_id("vehicle", i),
            "license_plate": f"{PROVINCE_LETTERS[i % len(PROVINCE_LETTERS)]}{1234 + i}",
            "brand": brand,
            "model": model,
            "status": "available",
        })
    return vehicles


def build_rentals(
    count: int,
    window_days: int,
    customers: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    promotions: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    now = datetime.now(UTC).replace(microsecond=0)
    rentals: list[dict[str, Any]] = []
    active_vehicle_ids: set[ObjectId] = set()
    for i in range(count):
        customer = customers[i % len(customers)]
        vehicle = vehicles[(i * 5) % len(vehicles)]
        start = (now - timedelta(days=rng.randint(0, window_days - 1), hours=rng.randint(0, 18))).replace(minute=0, second=0)
        duration_days = rng.randint(3, 5)
        return_date = start + timedelta(days=duration_days)
        status = "cancelled" if i % 17 == 0 else ("returned" if return_date <= now else "active")
        if status == "active" and vehicle["_id"] in active_vehicle_ids:
            status = "returned"
        if status == "active":
            active_vehicle_ids.add(vehicle["_id"])
            vehicle["status"] = "rented"

        adjustment_days = 0
        adjustment_reason = None
        promotion_id = None
        if i % 10 == 3:
            adjustment_days = 7
            adjustment_reason = "ชดเชยจากปัญหาการใช้งานระบบล็อกรถ"
        elif i % 7 == 2:
            promotion = promotions[i % 3]
            promotion_id = promotion["_id"]
            adjustment_days = promotion["extra_days"]
            adjustment_reason = f"ได้รับสิทธิ์จากโปรโมชั่น {promotion['code']}"

        document: dict[str, Any] = {
            "_id": stable_id("rental", i),
            "customer_id": customer["_id"],
            "vehicle_id": vehicle["_id"],
            "start_date": start,
            "return_date": return_date + timedelta(days=adjustment_days),
            "adjustment_days": adjustment_days,
            "adjustment_reason": adjustment_reason,
            "status": status,
        }
        if promotion_id is not None:
            document["promotion_id"] = promotion_id
        if status == "returned":
            document["actual_return_date"] = min(now, return_date + timedelta(hours=rng.randint(-4, 8)))
        rentals.append(document)
    return rentals


def build_payments(rentals: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    now = datetime.now(UTC).replace(microsecond=0)
    payments = []
    for i, rental in enumerate(rentals):
        amount = 3200 + (i % 5) * 650
        due_date = rental["start_date"] + timedelta(days=rng.randint(1, 3))
        if i % 11 == 4:
            due_date = now - timedelta(days=1 + i % 3)
            paid, status = 0, "overdue"
        elif i % 9 == 2:
            paid, status = amount // 2, "partial"
        elif rental["status"] == "active" and i % 3 == 0:
            due_date = now + timedelta(days=1 + i % 2)
            paid, status = 0, "pending"
        else:
            paid, status = amount, "paid"
        payments.append({
            "_id": stable_id("payment", i),
            "customer_id": rental["customer_id"],
            "rental_id": rental["_id"],
            "amount": money(amount),
            "paid_amount": money(paid),
            "due_date": due_date,
            "status": status,
        })
    return payments


def build_maintenance(
    vehicles: list[dict[str, Any]],
    window_days: int,
    rentals: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    now = datetime.now(UTC).replace(microsecond=0)
    descriptions = [
        "ตรวจเช็กระบบเบรกและเปลี่ยนผ้าเบรก", "อัปเดตซอฟต์แวร์ควบคุมแบตเตอรี่",
        "ตรวจระบบปรับอากาศ", "เปลี่ยนยางหน้าและตั้งศูนย์ล้อ",
        "แก้ไขเซนเซอร์ประตูฝั่งคนขับ", "ตรวจพอร์ตชาร์จและสายไฟแรงดันสูง",
    ]
    records = []
    maintenance_candidates = [vehicle for vehicle in vehicles if vehicle["status"] == "available"]
    if not maintenance_candidates:
        # A dense mock rental set can temporarily mark every vehicle as rented.
        # Reserve deterministic vehicles for open maintenance and close only their
        # active mock rentals so vehicle/rental state remains internally consistent.
        maintenance_candidates = vehicles[::5]
        if rentals is not None:
            candidate_ids = {vehicle["_id"] for vehicle in maintenance_candidates}
            for rental in rentals:
                if rental["vehicle_id"] in candidate_ids and rental["status"] == "active":
                    rental["status"] = "returned"
                    rental["actual_return_date"] = now
    for i in range(12):
        opened = now - timedelta(days=i % window_days, hours=2 + i)
        completed = i % 5 != 0
        vehicle = (vehicles[(i * 2) % len(vehicles)] if completed
                   else maintenance_candidates[i % len(maintenance_candidates)])
        if not completed:
            vehicle["status"] = "maintenance"
        records.append({
            "_id": stable_id("maintenance", i),
            "vehicle_id": vehicle["_id"],
            "opened_at": opened,
            "completed_at": opened + timedelta(hours=4 + i % 6) if completed else None,
            "description": descriptions[i % len(descriptions)],
            "status": "completed" if completed else "in_progress",
        })
    return records


def build_incidents(rentals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(UTC).replace(microsecond=0)
    descriptions = [
        "เฉี่ยวชนเสาขณะถอยจอด", "กระจกหน้าแตกร้าวจากเศษหิน",
        "ยางเสียหายระหว่างการเดินทาง", "กันชนมีรอยจากการเฉี่ยวชน",
    ]
    locations = ["กรุงเทพฯ", "นนทบุรี", "สมุทรปราการ", "ปทุมธานี"]
    incidents = []
    for i in range(8):
        rental = rentals[(i * 5 + 1) % len(rentals)]
        severity = ["minor", "minor", "major", "critical"][i % 4]
        status = "resolved" if i < 5 else ("investigating" if i < 7 else "reported")
        occurred_at = now - timedelta(days=i % 5, hours=i + 1)
        estimated_cost = 2500 + i * 1750
        incidents.append({
            "_id": stable_id("incident", i),
            "incident_code": f"INC-{i + 1:04d}",
            "rental_id": rental["_id"],
            "vehicle_id": rental["vehicle_id"],
            "customer_id": rental["customer_id"],
            "occurred_at": occurred_at,
            "severity": severity,
            "description": descriptions[i % len(descriptions)],
            "location": locations[i % len(locations)],
            "status": status,
            "estimated_cost": money(estimated_cost),
            "customer_liability": money(estimated_cost // 2 if severity != "critical" else estimated_cost),
            "resolved_at": occurred_at + timedelta(hours=8 + i) if status == "resolved" else None,
        })
    return incidents


def build_charging_sessions(rentals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(UTC).replace(microsecond=0)
    stations = ["EV Station Central", "Charge Hub Bangna", "Metro Charge Rangsit", "Fleet Depot"]
    sessions = []
    for i in range(24):
        rental = rentals[(i * 7 + 2) % len(rentals)]
        started_at = now - timedelta(days=i % 5, hours=(i * 3) % 20)
        status = "in_progress" if i in {0, 13} else ("failed" if i in {7, 19} else "completed")
        energy = Decimal("0") if status == "failed" else Decimal(str(18 + (i % 7) * 4.5))
        start_percent = 12 + (i * 7) % 35
        end_percent = start_percent if status == "failed" else min(100, start_percent + 45 + i % 25)
        sessions.append({
            "_id": stable_id("charging_session", i),
            "session_code": f"CHG-{i + 1:04d}",
            "vehicle_id": rental["vehicle_id"],
            "rental_id": rental["_id"],
            "customer_id": rental["customer_id"],
            "station_name": stations[i % len(stations)],
            "started_at": started_at,
            "ended_at": None if status == "in_progress" else started_at + timedelta(minutes=35 + i % 50),
            "energy_kwh": money(energy),
            "cost": money(energy * Decimal("7.25")),
            "start_battery_percent": start_percent,
            "end_battery_percent": end_percent,
            "status": status,
        })
    return sessions


def build_installments(
    rentals: list[dict[str, Any]], customers: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    now = datetime.now(UTC).replace(microsecond=0)
    plans, schedules = [], []
    for i in range(6):
        rental = rentals[(i * 7 + 3) % len(rentals)]
        customer = next(item for item in customers if item["_id"] == rental["customer_id"])
        installment_count = 4
        installment_amount = 3000 + i * 500
        plan_id = stable_id("installment_plan", i)
        plan_schedules = []
        for installment_no in range(1, installment_count + 1):
            due_date = now + timedelta(days=(installment_no - 2) * 7 + i)
            if installment_no == 2 and i % 3 == 1:
                due_date = now - timedelta(days=2 + i)
            if installment_no == 1:
                paid_amount, status, paid_at = installment_amount, "paid", due_date - timedelta(days=1)
            elif installment_no == 2 and i % 3 == 0:
                paid_amount, status, paid_at = installment_amount // 2, "partial", now - timedelta(hours=4)
            elif due_date < now:
                paid_amount, status, paid_at = 0, "overdue", None
            else:
                paid_amount, status, paid_at = 0, "pending", None
            schedule = {
                "_id": stable_id("installment_schedule", i * installment_count + installment_no - 1),
                "plan_id": plan_id,
                "installment_no": installment_no,
                "due_date": due_date,
                "amount": money(installment_amount),
                "paid_amount": money(paid_amount),
                "paid_at": paid_at,
                "status": status,
            }
            plan_schedules.append(schedule)
            schedules.append(schedule)
        plan_status = "defaulted" if any(x["status"] == "overdue" for x in plan_schedules) else "active"
        plans.append({
            "_id": plan_id,
            "plan_code": f"PLAN-{i + 1:04d}",
            "customer_id": customer["_id"],
            "rental_id": rental["_id"],
            "total_amount": money(installment_amount * installment_count),
            "down_payment": money(installment_amount),
            "installment_count": installment_count,
            "installment_amount": money(installment_amount),
            "start_date": now - timedelta(days=10 - i),
            "status": plan_status,
        })
    return plans, schedules


def build_campaigns(
    customers: list[dict[str, Any]], rentals: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    now = datetime.now(UTC).replace(microsecond=0)
    definitions = [
        ("EV First Drive", "social", 25000, "active"),
        ("Corporate Green Fleet", "email", 40000, "active"),
        ("Weekend Electric Escape", "partner", 18000, "completed"),
        ("Win-back August", "sms", 12000, "active"),
    ]
    campaigns, engagements = [], []
    for i, (name, channel, budget, status) in enumerate(definitions):
        campaign_id = stable_id("campaign", i)
        campaigns.append({
            "_id": campaign_id,
            "campaign_code": f"CMP-{i + 1:04d}",
            "name": name,
            "channel": channel,
            "start_date": now - timedelta(days=7 + i),
            "end_date": now + timedelta(days=14 - i) if status == "active" else now - timedelta(days=1),
            "budget": money(budget),
            "status": status,
        })
        for j in range(6):
            index = i * 6 + j
            customer = customers[(index * 3 + i) % len(customers)]
            outcome = ["delivered", "opened", "interested", "converted", "converted", "opted_out"][j]
            rental = (next(item for item in rentals if item["customer_id"] == customer["_id"])
                      if outcome == "converted" else None)
            document = {
                "_id": stable_id("campaign_engagement", index),
                "campaign_id": campaign_id,
                "customer_id": customer["_id"],
                "contacted_at": now - timedelta(days=j, hours=i),
                "outcome": outcome,
                "attributed_revenue": money(4500 + index * 125 if outcome == "converted" else 0),
            }
            if rental is not None:
                document["rental_id"] = rental["_id"]
            engagements.append(document)
    return campaigns, engagements


def upsert_documents(database, collection_name: str, documents: list[dict[str, Any]]) -> int:
    operations = [ReplaceOne({"_id": doc["_id"]}, doc, upsert=True) for doc in documents]
    if not operations:
        return 0
    database[collection_name].bulk_write(operations, ordered=False)
    return len(operations)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed linked mock ERP data into MongoDB")
    parser.add_argument("--rentals", type=int, default=48, choices=range(1, 501), metavar="1-500")
    parser.add_argument("--days", type=int, default=5, choices=range(3, 6), metavar="3-5")
    parser.add_argument("--random-seed", type=int, default=20260817)
    parser.add_argument("--confirm-seed", action="store_true", help="Required safety confirmation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm_seed:
        raise SystemExit("Refusing to write: add --confirm-seed after checking MONGODB_SEED_URI")
    load_dotenv()
    uri = (os.getenv("MONGODB_SEED_URI", "").strip()
           or os.getenv("MONGODB_URI", "").strip())
    database_name = os.getenv("MONGODB_DATABASE", "").strip()
    if not uri:
        raise SystemExit("MONGODB_URI (or optional MONGODB_SEED_URI) is required")
    if not database_name:
        raise SystemExit("MONGODB_DATABASE is required")

    rng = random.Random(args.random_seed)
    customers = build_customers()
    promotions = build_promotions()
    vehicles = build_vehicles()
    rentals = build_rentals(args.rentals, args.days, customers, vehicles, promotions, rng)
    payments = build_payments(rentals, rng)
    maintenance = build_maintenance(vehicles, args.days, rentals)
    incidents = build_incidents(rentals)
    charging_sessions = build_charging_sessions(rentals)
    installment_plans, installment_schedules = build_installments(rentals, customers)
    campaigns, campaign_engagements = build_campaigns(customers, rentals)
    datasets = {
        "customers": customers,
        "promotions": promotions,
        "vehicles": vehicles,
        "rentals": rentals,
        "payments": payments,
        "maintenance": maintenance,
        "incidents": incidents,
        "charging_sessions": charging_sessions,
        "installment_plans": installment_plans,
        "installment_schedules": installment_schedules,
        "campaigns": campaigns,
        "campaign_engagements": campaign_engagements,
    }

    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="erp-ai-agent-mock-seeder")
    try:
        database = client[database_name]
        database.command("ping")
        counts = {name: upsert_documents(database, name, docs) for name, docs in datasets.items()}
    finally:
        client.close()

    print(f"Seed completed for database: {database_name}")
    for name, count in counts.items():
        print(f"  {name}: {count} mock documents upserted")
    print(f"  total: {sum(counts.values())} linked documents")
    print("Re-running with the same options updates the same deterministic mock records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
