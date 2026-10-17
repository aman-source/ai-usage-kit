from fastapi.testclient import TestClient

from svc_api.app import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_get_order():
    assert client.get("/api/v1/orders/3").status_code == 200
