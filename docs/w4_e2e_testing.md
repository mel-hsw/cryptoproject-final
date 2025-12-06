# End-to-End Testing and Validation

This document describes the end-to-end testing strategy for the crypto volatility detection pipeline, including Kafka replay testing and full pipeline validation.

---

## Overview

The E2E testing suite validates the entire pipeline from data ingestion through prediction serving:

1. **Docker Compose Startup** - All services start correctly
2. **Data Replay** - Historical 10-minute dataset replayed to Kafka
3. **Feature Processing** - Featurizer consumes and processes data
4. **Model Predictions** - Prediction service generates volatility scores
5. **Metrics Collection** - Prometheus and monitoring dashboards track performance
6. **API Validation** - `/predict` endpoint responds with valid predictions

---

## Test Scripts

### 1. Kafka Replay Script (`scripts/replay_data.py`)

Replays historical tick data to Kafka for testing and validation.

**Features:**
- Reads NDJSON tick files and publishes to Kafka
- Configurable replay speed (1x, 10x, 100x, or instant)
- Optional timestamp updating for current-time testing
- Progress logging and error handling
- Kafka reconnection logic

**Usage:**

```bash
# Replay at 10x speed (10-minute dataset replays in 1 minute)
python scripts/replay_data.py \
  --file data/raw/ticks_BTCUSD_20251201_164446_10min.ndjson \
  --speed 10

# Replay first 100 messages instantly
python scripts/replay_data.py \
  --file data/raw/ticks_BTCUSD_20251201_164446_10min.ndjson \
  --speed 0 \
  --max-messages 100

# Replay preserving original timestamps
python scripts/replay_data.py \
  --file data/raw/ticks_BTCUSD_20251201_164446_10min.ndjson \
  --speed 1.0 \
  --preserve-timestamps
```

**Parameters:**
- `--file` - Path to NDJSON data file (required)
- `--speed` - Replay speed multiplier (default: 1.0)
  - `1.0` = real-time
  - `10.0` = 10x faster
  - `0` = instant (no delay)
- `--max-messages` - Maximum messages to replay (default: all)
- `--topic` - Kafka topic (default: `ticks.raw`)
- `--preserve-timestamps` - Keep original timestamps (default: update to current time)

---

### 2. End-to-End Validation Script (`scripts/e2e_validation.py`)

Comprehensive validation of the entire pipeline.

**Features:**
- Automated Docker Compose startup
- Service health checks
- API endpoint validation (`/health`, `/version`, `/predict`)
- Historical data replay
- Latency and performance validation
- Prometheus metrics verification
- JSON report generation

**Usage:**

```bash
# Full validation (starts Docker, replays data, checks everything)
python scripts/e2e_validation.py

# Quick validation (assumes services running, skips replay)
python scripts/e2e_validation.py --no-docker --quick

# Validation with services already running
python scripts/e2e_validation.py --no-docker
```

**Parameters:**
- `--no-docker` - Skip Docker service startup (assumes running)
- `--quick` - Quick mode (skip data replay or replay only 100 messages)

**Output:**
- Console output with step-by-step validation
- JSON report saved to `e2e_validation_report.json`
- Exit code 0 on success, 1 on failure

---

## Complete E2E Test Procedure

### Step 1: Clean Environment

```bash
# Stop all services and clean volumes
docker compose -f docker/compose.yaml down -v

# Verify clean slate
docker ps
```

### Step 2: Run Full Validation

```bash
# Run end-to-end validation
python scripts/e2e_validation.py
```

This will:
1. Start all Docker services (Kafka, API, featurizer, etc.)
2. Wait for services to be healthy (30s)
3. Check API `/health` endpoint
4. Check API `/version` endpoint
5. Test `/predict` endpoint with multiple scenarios
6. Replay 10-minute historical dataset at 10x speed
7. Wait for pipeline to process data (20s)
8. Check Prometheus metrics
9. Generate validation report

**Expected Duration:** ~3-5 minutes

### Step 3: Review Results

The script outputs a validation summary:

```
============================================================
VALIDATION SUMMARY
============================================================
Status: PASSED
Duration: 245.32s
Tests Passed: 7/7

✓ docker_services: PASSED
✓ api_health: PASSED
✓ api_version: PASSED
✓ predictions: PASSED
✓ data_replay: PASSED
✓ prometheus_metrics: PASSED

Full report saved to: e2e_validation_report.json
============================================================
```

### Step 4: Inspect JSON Report

```bash
cat e2e_validation_report.json
```

Example report:

```json
{
  "start_time": "2025-12-06T12:00:00.123456",
  "end_time": "2025-12-06T12:04:05.456789",
  "duration_seconds": 245.32,
  "status": "PASSED",
  "tests": {
    "docker_services": {
      "status": "passed",
      "services": ["kafka", "api", "featurizer", "prediction-consumer"]
    },
    "api_health": {
      "status": "passed",
      "data": {
        "status": "healthy",
        "model_loaded": true,
        "kafka_connected": true
      }
    },
    "predictions": {
      "status": "passed",
      "test_cases": 3,
      "avg_latency_ms": 45.23,
      "max_latency_ms": 78.91,
      "slo_pass": true
    },
    "data_replay": {
      "status": "passed",
      "speed": 10
    },
    "prometheus_metrics": {
      "status": "passed",
      "found_metrics": ["http_requests_total", "prediction_latency_seconds", "predictions_total"]
    }
  }
}
```

---

## Manual Testing Workflow

If you prefer manual testing, follow these steps:

### 1. Start Services

```bash
docker compose -f docker/compose.yaml up -d
sleep 30  # Wait for services to initialize
```

### 2. Check Service Health

```bash
# API health
curl http://localhost:8000/health

# Model version
curl http://localhost:8000/version
```

### 3. Test Predictions

```bash
# Single prediction
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'

# Expected response:
# {"scores":[0.74],"model_variant":"ml","version":"v1.2-random_forest","ts":"2025-12-06T12:00:00Z"}
```

### 4. Replay Historical Data

```bash
# Replay 10-minute dataset at 10x speed
python scripts/replay_data.py \
  --file data/raw/ticks_BTCUSD_20251201_164446_10min.ndjson \
  --speed 10
```

### 5. Monitor Pipeline

```bash
# Watch featurizer logs
docker compose -f docker/compose.yaml logs -f featurizer

# Watch prediction consumer logs
docker compose -f docker/compose.yaml logs -f prediction-consumer

# Check Prometheus metrics
curl http://localhost:8000/metrics
```

### 6. Check Grafana Dashboard

Open http://localhost:3000 (login: admin/admin123)
- Navigate to crypto-volatility-api dashboard
- Verify metrics are updating:
  - Request count increasing
  - Latency p50, p95 under SLO
  - Prediction scores being recorded

---

## CI/CD Integration

### GitHub Actions Workflow

Add E2E tests to your CI/CD pipeline:

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e-test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'

    - name: Install dependencies
      run: |
        pip install -r requirements.txt

    - name: Run E2E validation
      run: |
        python scripts/e2e_validation.py

    - name: Upload test report
      if: always()
      uses: actions/upload-artifact@v3
      with:
        name: e2e-validation-report
        path: e2e_validation_report.json
```

---

## Performance Benchmarks

### Expected Metrics

Based on the 10-minute dataset replay:

| Metric | Expected Value | SLO Target |
|--------|---------------|------------|
| API Latency (p50) | ~30-50ms | < 400ms |
| API Latency (p95) | ~60-100ms | < 800ms |
| Throughput | 100+ req/s | > 50 req/s |
| Error Rate | < 0.1% | < 1% |
| Model Load Time | < 5s | < 10s |
| Service Startup | < 60s | < 120s |

### Load Testing

For more intensive testing, use the load test script:

```bash
# 1000 requests with 50 concurrent connections
python scripts/load_test.py \
  --url http://localhost:8000 \
  --requests 1000 \
  --concurrent 50
```

---

## Troubleshooting

### Services Don't Start

```bash
# Check Docker resources
docker system df

# Increase Docker memory to 8GB minimum
# View logs for specific service
docker compose -f docker/compose.yaml logs api
```

### Kafka Connection Errors

```bash
# Restart Kafka
docker compose -f docker/compose.yaml restart kafka

# Wait for Kafka to be ready
sleep 20

# Check Kafka logs
docker compose -f docker/compose.yaml logs kafka
```

### API Not Responding

```bash
# Check API container status
docker compose -f docker/compose.yaml ps api

# View API logs
docker compose -f docker/compose.yaml logs api

# Restart API
docker compose -f docker/compose.yaml restart api
```

### Data Replay Fails

```bash
# Verify data file exists
ls -lh data/raw/ticks_BTCUSD_20251201_164446_10min.ndjson

# Check Kafka is running
docker compose -f docker/compose.yaml ps kafka

# Test with smaller batch
python scripts/replay_data.py \
  --file data/raw/ticks_BTCUSD_20251201_164446_10min.ndjson \
  --speed 0 \
  --max-messages 10
```

---

## Test Data

### 10-Minute Dataset

**File:** `data/raw/ticks_BTCUSD_20251201_164446_10min.ndjson`

**Properties:**
- Format: NDJSON (newline-delimited JSON)
- Duration: 10 minutes of BTC-USD tick data
- Source: Coinbase WebSocket API
- Size: ~2.2 MB
- Messages: ~2000 ticks

**Sample Record:**

```json
{
  "timestamp": "2025-12-01T16:44:46.123456Z",
  "product_id": "BTC-USD",
  "price": "98765.43",
  "volume_24h": "12345.67",
  "low_24h": "97000.00",
  "high_24h": "99500.00",
  "best_bid": "98765.42",
  "best_ask": "98765.44",
  "raw": { ... }
}
```

---

## Success Criteria

A successful E2E validation should demonstrate:

1. ✅ **Docker Services** - All required services start and remain healthy
2. ✅ **API Health** - `/health` endpoint returns `{"status":"healthy"}`
3. ✅ **Model Loaded** - `/version` shows correct model variant
4. ✅ **Predictions** - `/predict` returns valid scores [0, 1]
5. ✅ **Latency SLO** - p95 latency < 800ms
6. ✅ **Data Pipeline** - Kafka → Featurizer → Predictions flow works
7. ✅ **Metrics** - Prometheus metrics are collected and exposed
8. ✅ **No Errors** - Zero errors or exceptions in service logs

---

## Next Steps

After successful E2E validation:

1. **Demo Recording** - Follow [DEMO_PREP.md](DEMO_PREP.md) for 8-minute demo
2. **Load Testing** - Run `scripts/load_test.py` for performance validation
3. **Integration Tests** - Run `pytest scripts/test_api_integration.py`
4. **Production Deployment** - Deploy with confidence knowing the pipeline works

---

## References

- [Demo Preparation Guide](DEMO_PREP.md)
- [API Integration Tests](../scripts/test_api_integration.py)
- [Load Test Script](../scripts/load_test.py)
- [Replay Script](../scripts/replay_data.py)
- [E2E Validation Script](../scripts/e2e_validation.py)
