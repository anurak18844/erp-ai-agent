from fastapi.testclient import TestClient

from app import app, get_dashboard_reader


def test_dashboard_page_is_served():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "WeDrive ERP" in response.text
    assert "/api/dashboard" in response.text
    assert "chart" not in response.text.lower()


def test_dashboard_api_uses_read_model():
    expected = {
        "generated_at": "2026-08-17T10:00:00+00:00",
        "database": "test",
        "summary": {
            "customers": 20,
            "vehicles": 18,
            "active_rentals": 14,
            "outstanding_payments": 12,
            "outstanding_amount": "54000.00",
            "unfinished_maintenance": 3,
            "open_incidents": 2,
        },
        "vehicle_status": [],
        "payment_status": [],
        "active_rentals": [],
        "outstanding_payments": [],
        "maintenance": [],
        "incidents": [],
        "collection_counts": {},
    }

    class FakeReader:
        def read(self):
            return expected

    app.dependency_overrides[get_dashboard_reader] = lambda: FakeReader()
    try:
        response = TestClient(app).get("/api/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == expected
