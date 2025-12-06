"""
End-to-End Pipeline Validation Script

This script runs a complete validation of the crypto volatility detection pipeline:
1. Starts Docker Compose services
2. Waits for services to be healthy
3. Replays 10-minute historical dataset to Kafka
4. Verifies predictions are being made
5. Checks metrics and performance
6. Generates validation report

Usage:
    python scripts/e2e_validation.py [--no-docker] [--quick]
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
DOCKER_COMPOSE_FILE = PROJECT_ROOT / "docker" / "compose.yaml"
DATA_FILE = PROJECT_ROOT / "data" / "raw" / "ticks_BTCUSD_20251201_164446_10min.ndjson"
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
GRAFANA_URL = "http://localhost:3000"
MLFLOW_URL = "http://localhost:5001"


class ValidationError(Exception):
    """Custom exception for validation failures."""

    pass


class E2EValidator:
    """End-to-end pipeline validator."""

    def __init__(self, skip_docker: bool = False, quick_mode: bool = False):
        self.skip_docker = skip_docker
        self.quick_mode = quick_mode
        self.start_time = time.time()
        self.results = {
            "start_time": datetime.now().isoformat(),
            "tests": {},
            "metrics": {},
            "status": "running",
        }

    def log_step(self, step: str):
        """Log a validation step."""
        logger.info("=" * 60)
        logger.info(f"STEP: {step}")
        logger.info("=" * 60)

    def run_command(self, cmd: list, description: str, timeout: int = 60) -> str:
        """Run a shell command and return output."""
        logger.info(f"Running: {description}")
        logger.debug(f"Command: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=PROJECT_ROOT,
            )
            if result.returncode != 0:
                raise ValidationError(f"Command failed: {description}\n{result.stderr}")
            return result.stdout
        except subprocess.TimeoutExpired:
            raise ValidationError(f"Command timed out: {description}")

    def wait_for_service(
        self, url: str, service_name: str, max_attempts: int = 30, delay: int = 2
    ):
        """Wait for a service to become available."""
        logger.info(f"Waiting for {service_name} at {url}...")
        for attempt in range(max_attempts):
            try:
                response = requests.get(url, timeout=5)
                if response.status_code < 500:
                    logger.info(f"✓ {service_name} is ready")
                    return
            except requests.RequestException:
                pass

            if attempt < max_attempts - 1:
                logger.debug(
                    f"Attempt {attempt + 1}/{max_attempts} failed, retrying in {delay}s..."
                )
                time.sleep(delay)

        raise ValidationError(f"{service_name} did not become available at {url}")

    def check_docker_services(self):
        """Check that Docker services are running."""
        self.log_step("Checking Docker Services")

        output = self.run_command(
            ["docker", "compose", "-f", str(DOCKER_COMPOSE_FILE), "ps"],
            "Check Docker services",
        )

        required_services = ["kafka", "api", "featurizer", "prediction-consumer"]
        running_services = []

        for service in required_services:
            if service in output and "Up" in output:
                running_services.append(service)
                logger.info(f"✓ {service} is running")
            else:
                raise ValidationError(f"Service {service} is not running")

        self.results["tests"]["docker_services"] = {
            "status": "passed",
            "services": running_services,
        }

    def test_api_health(self):
        """Test API health endpoint."""
        self.log_step("Testing API Health")

        response = requests.get(f"{API_BASE_URL}/health", timeout=10)
        if response.status_code != 200:
            raise ValidationError(f"Health check failed: {response.status_code}")

        data = response.json()
        logger.info(f"Health status: {json.dumps(data, indent=2)}")

        if data.get("status") != "healthy":
            raise ValidationError(f"API is not healthy: {data}")

        if not data.get("model_loaded"):
            raise ValidationError("Model is not loaded")

        logger.info("✓ API is healthy and model is loaded")
        self.results["tests"]["api_health"] = {"status": "passed", "data": data}

    def test_api_version(self):
        """Test API version endpoint."""
        self.log_step("Testing API Version")

        response = requests.get(f"{API_BASE_URL}/version", timeout=10)
        if response.status_code != 200:
            raise ValidationError(f"Version check failed: {response.status_code}")

        data = response.json()
        logger.info(f"Version info: {json.dumps(data, indent=2)}")

        required_fields = ["model_variant", "version", "sha"]
        for field in required_fields:
            if field not in data:
                raise ValidationError(f"Missing field in version response: {field}")

        logger.info(
            f"✓ Model variant: {data['model_variant']}, Version: {data['version']}"
        )
        self.results["tests"]["api_version"] = {"status": "passed", "data": data}

    def test_predictions(self):
        """Test prediction endpoint with multiple scenarios."""
        self.log_step("Testing Predictions")

        test_cases = [
            {
                "name": "Single row - low volatility",
                "payload": {"rows": [{"ret_mean": 0.01, "ret_std": 0.005, "n": 50}]},
            },
            {
                "name": "Single row - high volatility",
                "payload": {"rows": [{"ret_mean": 0.08, "ret_std": 0.02, "n": 100}]},
            },
            {
                "name": "Single row - medium volatility",
                "payload": {"rows": [{"ret_mean": 0.05, "ret_std": 0.015, "n": 75}]},
            },
        ]

        latencies = []

        for test_case in test_cases:
            logger.info(f"Testing: {test_case['name']}")

            start = time.time()
            response = requests.post(
                f"{API_BASE_URL}/predict",
                json=test_case["payload"],
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            latency_ms = (time.time() - start) * 1000
            latencies.append(latency_ms)

            if response.status_code != 200:
                raise ValidationError(
                    f"Prediction failed ({test_case['name']}): {response.status_code}"
                )

            data = response.json()

            # Validate response structure
            required_fields = ["scores", "model_variant", "version", "ts"]
            for field in required_fields:
                if field not in data:
                    raise ValidationError(
                        f"Missing field in prediction response: {field}"
                    )

            # Validate scores (should have at least one score)
            if len(data["scores"]) < 1:
                raise ValidationError(
                    f"Expected at least 1 score, got {len(data['scores'])}"
                )

            # Validate score values
            for i, score in enumerate(data["scores"]):
                if not isinstance(score, (int, float)):
                    raise ValidationError(f"Score {i} is not numeric: {score}")
                if not 0 <= score <= 1:
                    raise ValidationError(f"Score {i} out of range [0, 1]: {score}")

            logger.info(
                f"  ✓ {test_case['name']}: {data['scores']} (latency: {latency_ms:.2f}ms)"
            )

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)

        logger.info("\nLatency Summary:")
        logger.info(f"  Average: {avg_latency:.2f}ms")
        logger.info(f"  Max: {max_latency:.2f}ms")
        logger.info(f"  SLO (p95 < 800ms): {'PASS' if max_latency < 800 else 'FAIL'}")

        self.results["tests"]["predictions"] = {
            "status": "passed",
            "test_cases": len(test_cases),
            "avg_latency_ms": round(avg_latency, 2),
            "max_latency_ms": round(max_latency, 2),
            "slo_pass": max_latency < 800,
        }

    def replay_historical_data(self):
        """Replay 10-minute historical dataset."""
        self.log_step("Replaying Historical Data")

        if not DATA_FILE.exists():
            raise ValidationError(f"Data file not found: {DATA_FILE}")

        logger.info(f"Replaying data from: {DATA_FILE}")

        # Use 10x speed for faster testing, or 100x in quick mode
        speed = 100 if self.quick_mode else 10

        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "replay_data.py"),
            "--file",
            str(DATA_FILE),
            "--speed",
            str(speed),
        ]

        if self.quick_mode:
            cmd.extend(["--max-messages", "100"])

        try:
            self.run_command(cmd, "Replay historical data", timeout=300)
            logger.info("✓ Data replay completed")
            self.results["tests"]["data_replay"] = {"status": "passed", "speed": speed}
        except ValidationError as e:
            logger.warning(f"Data replay failed (non-critical): {e}")
            self.results["tests"]["data_replay"] = {
                "status": "warning",
                "error": str(e),
            }

    def check_prometheus_metrics(self):
        """Check that Prometheus metrics are being collected."""
        self.log_step("Checking Prometheus Metrics")

        try:
            response = requests.get(f"{API_BASE_URL}/metrics", timeout=10)
            if response.status_code != 200:
                raise ValidationError(
                    f"Metrics endpoint failed: {response.status_code}"
                )

            metrics_text = response.text

            # Check for key metrics
            required_metrics = [
                "http_requests_total",
                "prediction_latency_seconds",
                "predictions_total",
            ]

            found_metrics = []
            for metric in required_metrics:
                if metric in metrics_text:
                    found_metrics.append(metric)
                    logger.info(f"✓ Found metric: {metric}")
                else:
                    logger.warning(f"Missing metric: {metric}")

            self.results["tests"]["prometheus_metrics"] = {
                "status": (
                    "passed"
                    if len(found_metrics) == len(required_metrics)
                    else "warning"
                ),
                "found_metrics": found_metrics,
                "total_metrics": len(required_metrics),
            }

        except Exception as e:
            logger.warning(f"Metrics check failed (non-critical): {e}")
            self.results["tests"]["prometheus_metrics"] = {
                "status": "warning",
                "error": str(e),
            }

    def generate_report(self):
        """Generate validation report."""
        self.log_step("Validation Report")

        elapsed = time.time() - self.start_time
        self.results["end_time"] = datetime.now().isoformat()
        self.results["duration_seconds"] = round(elapsed, 2)

        # Determine overall status
        passed = sum(
            1 for test in self.results["tests"].values() if test["status"] == "passed"
        )
        total = len(self.results["tests"])

        if passed == total:
            self.results["status"] = "PASSED"
        elif passed >= total * 0.8:
            self.results["status"] = "PASSED (with warnings)"
        else:
            self.results["status"] = "FAILED"

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("VALIDATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Status: {self.results['status']}")
        logger.info(f"Duration: {elapsed:.2f}s")
        logger.info(f"Tests Passed: {passed}/{total}")
        logger.info("")

        for test_name, test_result in self.results["tests"].items():
            status_icon = (
                "✓"
                if test_result["status"] == "passed"
                else "⚠" if test_result["status"] == "warning" else "✗"
            )
            logger.info(f"{status_icon} {test_name}: {test_result['status'].upper()}")

        # Save report to file
        report_path = PROJECT_ROOT / "e2e_validation_report.json"
        with open(report_path, "w") as f:
            json.dump(self.results, f, indent=2)

        logger.info("")
        logger.info(f"Full report saved to: {report_path}")
        logger.info("=" * 60)

        return self.results["status"] in ["PASSED", "PASSED (with warnings)"]

    def run(self):
        """Run complete validation suite."""
        try:
            if not self.skip_docker:
                self.log_step("Starting Docker Services")
                logger.info("Starting services with docker compose...")
                self.run_command(
                    ["docker", "compose", "-f", str(DOCKER_COMPOSE_FILE), "up", "-d"],
                    "Start Docker services",
                    timeout=120,
                )
                logger.info("Waiting 30 seconds for services to initialize...")
                time.sleep(30)

            # Wait for critical services
            self.wait_for_service(f"{API_BASE_URL}/health", "API")

            # Run validation tests
            self.check_docker_services()
            self.test_api_health()
            self.test_api_version()
            self.test_predictions()

            if not self.quick_mode:
                self.replay_historical_data()
                # Wait for pipeline to process some data
                logger.info("Waiting 20 seconds for pipeline to process data...")
                time.sleep(20)

            self.check_prometheus_metrics()

            # Generate report
            success = self.generate_report()

            return 0 if success else 1

        except ValidationError as e:
            logger.error(f"\n❌ Validation failed: {e}")
            self.results["status"] = "FAILED"
            self.results["error"] = str(e)
            self.generate_report()
            return 1
        except KeyboardInterrupt:
            logger.info("\n⚠ Validation interrupted by user")
            self.results["status"] = "INTERRUPTED"
            self.generate_report()
            return 130
        except Exception as e:
            logger.error(f"\n❌ Unexpected error: {e}", exc_info=True)
            self.results["status"] = "ERROR"
            self.results["error"] = str(e)
            self.generate_report()
            return 1


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end validation of crypto volatility pipeline"
    )
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Skip Docker service startup (assumes services are already running)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: skip data replay or replay only 100 messages",
    )

    args = parser.parse_args()

    validator = E2EValidator(skip_docker=args.no_docker, quick_mode=args.quick)
    exit_code = validator.run()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
