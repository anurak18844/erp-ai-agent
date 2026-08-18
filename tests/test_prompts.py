from agent.prompts import ANSWER_PROMPT, INTENT_PROMPT, PLAN_PROMPT, REPAIR_PROMPT
from tools.metadata_tool import MetadataCatalog


def test_answer_prompt_requires_natural_status_translation():
    assert "warm, capable service assistant" in ANSWER_PROMPT
    assert "translate internal enum/status values" in ANSWER_PROMPT
    assert "do not promise availability" in ANSWER_PROMPT
    assert "immutable factual ledger" in ANSWER_PROMPT
    assert "never move, repeat, redistribute" in ANSWER_PROMPT
    assert "must never be presented as the value of every row" in ANSWER_PROMPT
    assert "highest, lowest, total, trend, or recommendation" in ANSWER_PROMPT
    assert "does not prove the base" in ANSWER_PROMPT


def test_intent_prompt_restricts_domains_to_physical_collections():
    assert "available_collections" in INTENT_PROMPT
    assert "only from that list" in INTENT_PROMPT
    assert "never invent, singularize, translate, or rename" in INTENT_PROMPT


def test_relative_dates_must_use_server_runtime_context():
    assert "Never guess the current date" in PLAN_PROMPT
    assert "rolling N x 24 hour interval" in PLAN_PROMPT
    assert "do not extend its end into the future" in PLAN_PROMPT
    assert "only from runtime_context" in REPAIR_PROMPT
    assert "never change a past-N-days query" in ANSWER_PROMPT
    assert "never rewrite it as inclusive whole dates" in ANSWER_PROMPT
    assert "blindly project index 0" in PLAN_PROMPT
    assert "pre-aggregate each branch" in PLAN_PROMPT
    assert "fan-out join" in PLAN_PROMPT
    assert "unique _id" in PLAN_PROMPT
    assert "by vehicle_id" in PLAN_PROMPT
    assert "must `$match` status completed" in PLAN_PROMPT
    assert "vehicles.status = maintenance" in PLAN_PROMPT
    assert "lookup all related child rows" in PLAN_PROMPT


def test_plan_prompt_distinguishes_physical_names_from_free_aliases():
    assert "free query-local aliases" in PLAN_PROMPT
    assert "business_rules" in PLAN_PROMPT
    assert "literal ISO-8601" in REPAIR_PROMPT
    assert "$addToSet` includes null" in PLAN_PROMPT
    assert "declared multi-hop path" in PLAN_PROMPT
    assert "guessed sentinel" in PLAN_PROMPT
    assert "has_payment" in PLAN_PROMPT
    assert "actual payments.status, amount, paid_amount, and due_date" in PLAN_PROMPT
    assert "Never repair a failed lookup" in REPAIR_PROMPT


def test_payment_metadata_rejects_missing_document_as_unpaid(catalog: MetadataCatalog):
    payment = catalog.get("payments")
    charging = catalog.get("charging_sessions")
    assert any("no_payment" in rule for rule in payment["business_rules"])
    assert any("has_payment = true" in rule for rule in payment["business_rules"])
    assert any("charging_sessions.rental_id" in note for note in charging["notes"])
    assert any("payments.paid_amount" in note for note in charging["notes"])


def test_vehicle_metadata_defines_customer_facing_status_meanings(catalog: MetadataCatalog):
    rules = " ".join(catalog.get("vehicles")["business_rules"])
    assert "status = available" in rules
    assert "พร้อมให้เช่า" in rules
    assert "status = rented" in rules
    assert "ถูกเช่าอยู่" in rules
