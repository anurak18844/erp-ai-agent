from tools.metadata_tool import search_metadata


def test_all_yaml_load_and_required_sections(catalog):
    docs = catalog.load()
    assert len(docs) == 12
    for document in docs.values():
        assert {"collection", "description", "fields", "relationships", "business_rules", "notes"} <= document.keys()


def test_search_finds_adjustment_fields_and_business_rule(catalog):
    result = search_metadata("ทำไมได้วันเช่าเพิ่ม adjustment reason", catalog=catalog)
    rentals = next(item for item in result["candidates"] if item["collection"] == "rentals")
    maintenance = next(item for item in result["candidates"] if item["collection"] == "maintenance")
    assert rentals["selected"] is True
    assert rentals["score"] > maintenance["score"]
    assert "adjustment_reason" in result["metadata_context"]["rentals"]["fields"]
    assert result["metadata_context"]["rentals"]["business_rules"]


def test_search_includes_relevant_relationship_neighbor_beyond_limit(catalog):
    result = catalog.search(
        "rental customer vehicle payment promotion extra_days",
        limit=4,
    )
    assert "rentals" in result.metadata_context
    assert "promotions" in result.metadata_context


def test_search_uses_yaml_aliases_and_reverse_relationships(catalog):
    result = catalog.search("damage_report ของผู้เช่าและรถ", limit=2)
    assert "incidents" in result.metadata_context
    assert "rentals" in result.metadata_context
    assert "customers" in result.metadata_context

    customer_result = catalog.search("customer_info", limit=1)
    assert "customers" in customer_result.metadata_context


def test_explicit_collection_alias_is_not_dropped_from_complex_query(catalog):
    result = catalog.search(
        "สรุปต้นทุนและรายได้ตามรุ่นรถจาก payment charging cost และ incident estimated cost"
    )
    assert "payments" in result.metadata_context
