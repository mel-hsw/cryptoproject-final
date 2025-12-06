"""
Integration tests for the Crypto Volatility API.

Tests the /predict, /health, /version, and /metrics endpoints to ensure
the API is functioning correctly in the CI/CD pipeline.
"""

import os
import sys
import time
import requests
import pytest

# API base URL from environment or default to localhost
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="module")
def api_url():
    """Fixture to provide API base URL."""
    return API_BASE_URL


def test_health_endpoint(api_url):
    """Test that the /health endpoint returns 200 OK."""
    response = requests.get(f"{api_url}/health", timeout=10)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "status" in data, "Response should contain 'status' field"
    assert data["status"] == "healthy", f"Expected status 'healthy', got {data['status']}"


def test_version_endpoint(api_url):
    """Test that the /version endpoint returns model information."""
    response = requests.get(f"{api_url}/version", timeout=10)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "model_variant" in data, "Response should contain 'model_variant'"
    assert "version" in data, "Response should contain 'version'"

    # Verify model variant is either 'ml' or 'baseline'
    assert data["model_variant"] in ["ml", "baseline"], \
        f"model_variant should be 'ml' or 'baseline', got {data['model_variant']}"


def test_metrics_endpoint(api_url):
    """Test that the /metrics endpoint returns Prometheus metrics."""
    response = requests.get(f"{api_url}/metrics", timeout=10)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    # Verify response is text (Prometheus format)
    assert response.headers["content-type"].startswith("text/plain"), \
        "Metrics should be in Prometheus text format"

    # Verify some expected metrics are present
    metrics_text = response.text
    assert "api_request_duration_seconds" in metrics_text or "http_requests_total" in metrics_text, \
        "Expected to find standard API metrics"


def test_predict_endpoint_valid_input(api_url):
    """Test that the /predict endpoint accepts valid input and returns predictions."""
    payload = {
        "rows": [
            {
                "ret_mean": 0.05,
                "ret_std": 0.01,
                "n": 50
            }
        ]
    }

    response = requests.post(
        f"{api_url}/predict",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "scores" in data, "Response should contain 'scores'"
    assert "model_variant" in data, "Response should contain 'model_variant'"
    assert "version" in data, "Response should contain 'version'"
    assert "ts" in data, "Response should contain 'ts' (timestamp)"

    # Verify scores is a list
    assert isinstance(data["scores"], list), "scores should be a list"
    assert len(data["scores"]) == 1, "Should return one score for one input row"

    # Verify score is a float between 0 and 1 (probability)
    score = data["scores"][0]
    assert isinstance(score, (int, float)), "Score should be numeric"
    assert 0 <= score <= 1, f"Score should be between 0 and 1, got {score}"


def test_predict_endpoint_multiple_rows(api_url):
    """Test that the /predict endpoint handles multiple input rows."""
    payload = {
        "rows": [
            {"ret_mean": 0.05, "ret_std": 0.01, "n": 50},
            {"ret_mean": 0.02, "ret_std": 0.005, "n": 30},
            {"ret_mean": 0.08, "ret_std": 0.02, "n": 100}
        ]
    }

    response = requests.post(
        f"{api_url}/predict",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert len(data["scores"]) == 3, "Should return three scores for three input rows"

    # Verify all scores are valid probabilities
    for i, score in enumerate(data["scores"]):
        assert isinstance(score, (int, float)), f"Score {i} should be numeric"
        assert 0 <= score <= 1, f"Score {i} should be between 0 and 1, got {score}"


def test_predict_endpoint_invalid_input(api_url):
    """Test that the /predict endpoint handles invalid input gracefully."""
    # Test with missing required field
    payload = {
        "rows": [
            {
                "ret_mean": 0.05
                # Missing ret_std and n
            }
        ]
    }

    response = requests.post(
        f"{api_url}/predict",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10
    )

    # Should return 422 (Unprocessable Entity) for validation error
    # or 400 (Bad Request) depending on FastAPI configuration
    assert response.status_code in [400, 422, 500], \
        f"Expected error status code for invalid input, got {response.status_code}"


def test_predict_endpoint_empty_input(api_url):
    """Test that the /predict endpoint handles empty input."""
    payload = {"rows": []}

    response = requests.post(
        f"{api_url}/predict",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10
    )

    # Should handle empty input gracefully (either error or empty scores)
    assert response.status_code in [200, 400, 422], \
        f"Expected 200 or error code for empty input, got {response.status_code}"

    if response.status_code == 200:
        data = response.json()
        assert isinstance(data["scores"], list), "scores should be a list"


def test_api_latency(api_url):
    """Test that API responds within acceptable latency (based on SLO)."""
    payload = {
        "rows": [
            {"ret_mean": 0.05, "ret_std": 0.01, "n": 50}
        ]
    }

    start_time = time.time()
    response = requests.post(
        f"{api_url}/predict",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    latency_ms = (time.time() - start_time) * 1000

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    # SLO target: p95 < 800ms (we'll use 1000ms for CI tolerance)
    assert latency_ms < 1000, \
        f"API latency {latency_ms:.2f}ms exceeds 1000ms threshold (SLO: 800ms)"

    print(f"API latency: {latency_ms:.2f}ms (within SLO)")


if __name__ == "__main__":
    # Allow running tests directly with: python scripts/test_api_integration.py
    pytest.main([__file__, "-v", "--tb=short"])
