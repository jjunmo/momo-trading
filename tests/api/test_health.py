def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["result"] == "SUCCESS"
    assert response.json()["data"]["status"] == "healthy"
    assert "X-Request-ID" in response.headers
