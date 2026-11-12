def test_health_returns_ok_without_authentication(client):
    # No Authorization header is sent.
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_not_in_the_contract():
    # openapi.yaml has no /health path (checked at grooming). The schema comparison therefore
    # cannot cover this route; this test and the one above do. If the contract adds /health,
    # extend the comparison in test_backend_openapi.py and remove this assertion.
    from backend_support import load_contract

    assert "/health" not in load_contract()["paths"]
