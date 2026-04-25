"""Unit tests for the /arena route."""
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_arena_route_returns_200(client):
    response = client.get("/arena")
    assert response.status_code == 200
