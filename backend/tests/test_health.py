from django.test import Client


def test_health_returns_ok(client: Client) -> None:
    response = client.get("/api/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_payload_contains_no_extra_keys(client: Client) -> None:
    payload = client.get("/api/health/").json()

    assert list(payload) == ["status"]
