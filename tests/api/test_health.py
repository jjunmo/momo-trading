async def test_health_check(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["result"] == "SUCCESS"
    assert response.json()["data"]["status"] == "healthy"
