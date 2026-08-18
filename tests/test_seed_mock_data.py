import random
from datetime import UTC, datetime

from scripts.seed_mock_data import (
    build_customers,
    build_campaigns,
    build_charging_sessions,
    build_incidents,
    build_installments,
    build_maintenance,
    build_payments,
    build_promotions,
    build_rentals,
    build_vehicles,
    stable_id,
)


def test_seed_builds_48_linked_rentals_with_three_to_five_day_base_duration():
    customers = build_customers()
    promotions = build_promotions()
    vehicles = build_vehicles()
    rentals = build_rentals(48, 5, customers, vehicles, promotions, random.Random(20260817))
    payments = build_payments(rentals, random.Random(20260817))
    maintenance = build_maintenance(vehicles, 5, rentals)
    incidents = build_incidents(rentals)
    charging_sessions = build_charging_sessions(rentals)
    installment_plans, installment_schedules = build_installments(rentals, customers)
    campaigns, campaign_engagements = build_campaigns(customers, rentals)

    assert len(customers) == 20
    assert len(vehicles) == 18
    assert len(promotions) == 4
    assert len(rentals) == 48
    assert len(payments) == 48
    assert len(maintenance) == 12
    assert len(incidents) == 8
    assert len(charging_sessions) == 24
    assert len(installment_plans) == 6
    assert len(installment_schedules) == 24
    assert len(campaigns) == 4
    assert len(campaign_engagements) == 24

    customer_ids = {item["_id"] for item in customers}
    vehicle_ids = {item["_id"] for item in vehicles}
    rental_ids = {item["_id"] for item in rentals}
    assert all(item["customer_id"] in customer_ids for item in rentals)
    assert all(item["vehicle_id"] in vehicle_ids for item in rentals)
    assert all(item["rental_id"] in rental_ids for item in payments)
    assert all(item["rental_id"] in rental_ids for item in incidents)
    assert all(item["rental_id"] in rental_ids for item in charging_sessions)
    assert all(item["rental_id"] in rental_ids for item in installment_plans)
    plan_ids = {item["_id"] for item in installment_plans}
    assert all(item["plan_id"] in plan_ids for item in installment_schedules)
    campaign_ids = {item["_id"] for item in campaigns}
    assert all(item["campaign_id"] in campaign_ids for item in campaign_engagements)
    assert all(
        item.get("rental_id") in rental_ids
        for item in campaign_engagements if item["outcome"] == "converted"
    )
    assert all(
        item["status"] != "overdue" or item["due_date"] < datetime.now(UTC)
        for item in payments
    )
    active_vehicle_ids = {
        item["vehicle_id"] for item in rentals if item["status"] == "active"
    }
    open_maintenance_vehicle_ids = {
        item["vehicle_id"] for item in maintenance if item["status"] in {"open", "in_progress"}
    }
    assert active_vehicle_ids.isdisjoint(open_maintenance_vehicle_ids)

    base_rentals = [item for item in rentals if item["adjustment_days"] == 0]
    durations = {(item["return_date"] - item["start_date"]).days for item in base_rentals}
    assert durations <= {3, 4, 5}
    assert durations


def test_mock_ids_are_deterministic_for_safe_upsert():
    assert stable_id("rental", 7) == stable_id("rental", 7)
    assert stable_id("rental", 7) != stable_id("rental", 8)
