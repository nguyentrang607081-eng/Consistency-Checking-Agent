from fastapi.testclient import TestClient
from consistency_checking_agent.interface import app

def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
