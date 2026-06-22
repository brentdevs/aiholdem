"""Unit tests for the /model/<model_id> detail route and its JSON endpoints."""

import pytest

from app import create_app


@pytest.fixture
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.mark.asyncio
async def test_model_detail_route_renders_template(app):
    async with app.test_client() as client:
        response = await client.get("/model/google/gemini-2.5-flash")
        assert response.status_code == 200
        html = (await response.get_data()).decode()
        assert "Chart.js" in html or "chart.js" in html.lower()
        assert "gemini-2.5-flash" in html


@pytest.mark.asyncio
async def test_api_model_summary_returns_503_when_unavailable(app):
    async with app.test_client() as client:
        response = await client.get("/api/model/google/gemini-2.5-flash/summary")
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_api_model_daily_returns_503_when_unavailable(app):
    async with app.test_client() as client:
        response = await client.get("/api/model/google/gemini-2.5-flash/daily")
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_api_model_style_returns_503_when_unavailable(app):
    async with app.test_client() as client:
        response = await client.get("/api/model/google/gemini-2.5-flash/style")
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_api_model_hands_returns_503_when_unavailable(app):
    async with app.test_client() as client:
        response = await client.get("/api/model/google/gemini-2.5-flash/hands")
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_api_model_daily_accepts_days_query_param(app):
    async with app.test_client() as client:
        response = await client.get("/api/model/google/gemini-2.5-flash/daily?days=7")
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_api_model_style_accepts_buckets_query_param(app):
    async with app.test_client() as client:
        response = await client.get("/api/model/google/gemini-2.5-flash/style?buckets=5")
        assert response.status_code == 503
