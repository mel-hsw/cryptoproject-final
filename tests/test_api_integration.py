"""
Integration test for API endpoints.
Tests the /predict endpoint with sample data.
"""

import pytest
import httpx
import os
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def check_api_available(api_base_url: str) -> bool:
    """Check if API server is available."""
    try:
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(api_base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


# Sample feature data - Assignment API format (simple names)
SAMPLE_ROWS = [{"ret_mean": 0.05, "ret_std": 0.01, "n": 50}]

# Sample feature data - Full feature names (for legacy endpoint)
SAMPLE_FEATURES = {
    "log_return_300s": 0.001,
    "spread_mean_300s": 0.5,
    "trade_intensity_300s": 100,
    "order_book_imbalance_300s": 0.6,
    "spread_mean_60s": 0.3,
    "order_book_imbalance_60s": 0.55,
    "price_velocity_300s": 0.0001,
    "realized_volatility_300s": 0.002,
    "order_book_imbalance_30s": 0.52,
    "realized_volatility_60s": 0.0015,
}


@pytest.fixture
def api_base_url():
    """Get API base URL from environment or use default."""
    return os.getenv("API_BASE_URL", "http://localhost:8000")


@pytest.mark.asyncio
async def test_health_endpoint(api_base_url):
    """Test /health endpoint returns 200."""
    if not check_api_available(api_base_url):
        pytest.skip(f"API server not available at {api_base_url}")

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{api_base_url}/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "timestamp" in data
        assert "model_loaded" in data


@pytest.mark.asyncio
async def test_version_endpoint(api_base_url):
    """Test /version endpoint returns version info - Assignment API."""
    if not check_api_available(api_base_url):
        pytest.skip(f"API server not available at {api_base_url}")

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{api_base_url}/version")
        assert response.status_code == 200
        data = response.json()
        # Assignment API contract
        assert "model" in data
        assert "sha" in data
        assert "version" in data
        assert "model_variant" in data


@pytest.mark.asyncio
async def test_predict_endpoint(api_base_url):
    """Test /predict endpoint with Assignment API format."""
    if not check_api_available(api_base_url):
        pytest.skip(f"API server not available at {api_base_url}")

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Assignment API contract: {"rows": [...]} -> {"scores": [...]}
        response = await client.post(
            f"{api_base_url}/predict", json={"rows": SAMPLE_ROWS}
        )
        assert response.status_code == 200
        data = response.json()
        # Assignment API response format
        assert "scores" in data
        assert "model_variant" in data
        assert "version" in data
        assert "ts" in data
        assert isinstance(data["scores"], list)
        assert len(data["scores"]) == len(SAMPLE_ROWS)
        for score in data["scores"]:
            assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_predict_legacy_endpoint(api_base_url):
    """Test /predict/legacy endpoint with full feature names."""
    if not check_api_available(api_base_url):
        pytest.skip(f"API server not available at {api_base_url}")

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{api_base_url}/predict/legacy", json={"features": SAMPLE_FEATURES}
        )
        assert response.status_code == 200
        data = response.json()
        assert "prediction" in data
        assert "probability" in data
        assert "alert" in data
        assert "inference_time_ms" in data
        assert "model_version" in data
        assert isinstance(data["prediction"], int)
        assert 0 <= data["prediction"] <= 1
        assert 0.0 <= data["probability"] <= 1.0
        assert isinstance(data["alert"], bool)


@pytest.mark.asyncio
async def test_metrics_endpoint(api_base_url):
    """Test /metrics endpoint returns Prometheus format."""
    if not check_api_available(api_base_url):
        pytest.skip(f"API server not available at {api_base_url}")

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{api_base_url}/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")
        # Check for Prometheus metric format
        content = response.text
        assert (
            "http_requests_total" in content or "prediction_latency_seconds" in content
        )
