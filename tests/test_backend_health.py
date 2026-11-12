def test_health_returns_ok_without_authentication(client):
    # No Authorization header is sent.
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_in_the_contract():
    from backend_support import load_contract

    path = load_contract()["paths"]["/health"]
    assert set(path) == {"get"}
    operation = path["get"]
    assert operation["operationId"] == "getHealth"
    assert operation["security"] == []
    assert set(operation["responses"]) == {"200"}
