"""
Load test script for API /predict endpoint.
Sends 100 burst requests and measures latency metrics.
"""

import asyncio
import time
import statistics
import argparse
import httpx
from typing import List, Dict
import json
from pathlib import Path

# Sample feature data - Assignment API format
SAMPLE_REQUEST = {"rows": [{"ret_mean": 0.05, "ret_std": 0.01, "n": 50}]}

# Legacy feature format (for backward compatibility testing)
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


async def send_request(client: httpx.AsyncClient, url: str, request_id: int) -> Dict:
    """Send a single prediction request and measure latency."""
    start_time = time.time()
    try:
        response = await client.post(url, json=SAMPLE_REQUEST, timeout=10.0)
        elapsed_ms = (time.time() - start_time) * 1000

        return {
            "request_id": request_id,
            "status_code": response.status_code,
            "latency_ms": elapsed_ms,
            "success": 200 <= response.status_code < 300,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        return {
            "request_id": request_id,
            "status_code": None,
            "latency_ms": elapsed_ms,
            "success": False,
            "error": str(e),
        }


async def run_load_test(
    base_url: str, num_requests: int = 100, concurrent: int = 10
) -> Dict:
    """Run load test with burst requests."""
    url = f"{base_url}/predict"
    results: List[Dict] = []

    print("Starting load test:")
    print(f"  URL: {url}")
    print(f"  Total requests: {num_requests}")
    print(f"  Concurrent requests: {concurrent}")
    print()

    start_time = time.time()

    async with httpx.AsyncClient() as client:
        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrent)

        async def bounded_request(request_id: int):
            async with semaphore:
                return await send_request(client, url, request_id)

        # Create all tasks
        tasks = [bounded_request(i) for i in range(num_requests)]

        # Execute all requests concurrently (burst)
        results = await asyncio.gather(*tasks)

    total_time = time.time() - start_time

    # Calculate statistics
    latencies = [r["latency_ms"] for r in results]
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    stats = {
        "total_requests": num_requests,
        "successful_requests": len(successful),
        "failed_requests": len(failed),
        "success_rate": len(successful) / num_requests * 100,
        "total_time_seconds": total_time,
        "requests_per_second": num_requests / total_time if total_time > 0 else 0,
        "latency_ms": {
            "min": min(latencies) if latencies else 0,
            "max": max(latencies) if latencies else 0,
            "mean": statistics.mean(latencies) if latencies else 0,
            "median": statistics.median(latencies) if latencies else 0,
            "p50": statistics.median(latencies) if latencies else 0,
            "p95": (
                statistics.quantiles(latencies, n=20)[18]
                if len(latencies) >= 20
                else max(latencies) if latencies else 0
            ),
            "p99": (
                statistics.quantiles(latencies, n=100)[98]
                if len(latencies) >= 100
                else max(latencies) if latencies else 0
            ),
        },
        "errors": [r["error"] for r in failed if r["error"]],
    }

    return stats, results


def print_results(stats: Dict):
    """Print formatted test results."""
    print("=" * 60)
    print("LOAD TEST RESULTS")
    print("=" * 60)
    print(f"Total Requests:     {stats['total_requests']}")
    print(
        f"Successful:          {stats['successful_requests']} ({stats['success_rate']:.1f}%)"
    )
    print(f"Failed:               {stats['failed_requests']}")
    print(f"Total Time:           {stats['total_time_seconds']:.2f}s")
    print(f"Requests/Second:      {stats['requests_per_second']:.2f}")
    print()
    print("Latency (ms):")
    print(f"  Min:                {stats['latency_ms']['min']:.2f}")
    print(f"  Mean:               {stats['latency_ms']['mean']:.2f}")
    print(f"  Median (p50):       {stats['latency_ms']['p50']:.2f}")
    print(f"  p95:                {stats['latency_ms']['p95']:.2f}")
    print(f"  p99:                {stats['latency_ms']['p99']:.2f}")
    print(f"  Max:                {stats['latency_ms']['max']:.2f}")
    print()

    if stats["errors"]:
        print("Errors:")
        error_counts = {}
        for error in stats["errors"]:
            error_counts[error] = error_counts.get(error, 0) + 1
        for error, count in error_counts.items():
            print(f"  {error}: {count}")

    print("=" * 60)

    # Check SLO compliance
    p95_latency = stats["latency_ms"]["p95"]
    print()
    print("SLO Compliance:")
    p95_pass = "PASS" if p95_latency <= 800 else "FAIL"
    success_pass = "PASS" if stats["success_rate"] >= 99 else "FAIL"
    print(f"  p95 <= 800ms:       [{p95_pass}] ({p95_latency:.2f}ms)")
    print(f"  Success Rate >= 99%: [{success_pass}] ({stats['success_rate']:.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Load test API /predict endpoint")
    parser.add_argument(
        "--url", type=str, default="http://localhost:8000", help="API base URL"
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=100,
        help="Number of requests to send (default: 100)",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=10,
        help="Number of concurrent requests (default: 10)",
    )
    parser.add_argument(
        "--output", type=str, help="Output JSON file for detailed results"
    )

    args = parser.parse_args()

    # Run load test
    stats, results = asyncio.run(
        run_load_test(args.url, args.requests, args.concurrent)
    )

    # Print results
    print_results(stats)

    # Save detailed results if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump({"summary": stats, "results": results}, f, indent=2)
        print(f"\nDetailed results saved to: {output_path}")


if __name__ == "__main__":
    main()
