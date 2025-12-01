# Runbook: Crypto Volatility Detection API

## Table of Contents

1. [Startup](#startup)
2. [Health Checks](#health-checks)
3. [Troubleshooting](#troubleshooting)
4. [Recovery Procedures](#recovery-procedures)
5. [Failure Scenarios](#failure-scenarios)

---

## Startup

### Quick Start

```bash
# Start complete pipeline (handles Docker + Python processes)
./scripts/start_pipeline.sh

# Stop pipeline
./scripts/stop_pipeline.sh
```

**What it does:**
- Checks/starts Docker services (`docker compose -f docker/compose.yaml up -d`)
- Creates Kafka topics (`ticks.raw`, `ticks.features` - 3 partitions each)
- Starts 3 Python processes:
  1. `ws_ingest.py` - Coinbase WebSocket → Kafka
  2. `featurizer.py` - Raw ticks → Features → Kafka
  3. `prediction_consumer.py` - Features → `/predict` API

**Flow:** `Coinbase → Kafka (ticks.raw) → Features → Kafka (ticks.features) → /predict API → Metrics`

### Manual Setup

```bash
# 1. Python environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Start Docker services
docker compose -f docker/compose.yaml up -d

# 3. Verify services ready
curl http://localhost:8000/health
```

---

## Health Checks

```bash
# API health
curl http://localhost:8000/health
# Expected: {"status": "healthy", "model_loaded": true, "kafka_connected": true}

# Test prediction
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'

# Check services
curl http://localhost:9090/-/healthy  # Prometheus
curl http://localhost:3000/api/health  # Grafana
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092  # Kafka
```

**Monitoring:**
- Grafana: http://localhost:3000 (admin/admin)
- Prometheus: http://localhost:9090
- API Metrics: http://localhost:8000/metrics

---

## Troubleshooting

### API Returns 503 "Model not loaded"

```bash
# Check model file
ls -lh models/artifacts/random_forest/model.pkl

# Check logs
docker logs volatility-api

# Fix: Restart API
docker compose -f docker/compose.yaml restart api
```

### Kafka Connection Failed

```bash
# Check Kafka status
docker compose -f docker/compose.yaml ps kafka

# Test connectivity
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092

# Fix: Restart Kafka + API + pipeline
docker compose -f docker/compose.yaml restart kafka
sleep 20
docker compose -f docker/compose.yaml restart api
./scripts/start_pipeline.sh  # Pipeline processes auto-reconnect
```

### High Latency (p95 > 800ms)

```bash
# Check resources
docker stats volatility-api

# Check errors
docker logs volatility-api | grep -i error

# Run load test
python scripts/load_test.py --requests 100
```

### Prometheus Not Scraping

```bash
# Check targets
curl http://localhost:9090/api/v1/targets

# Check API metrics endpoint
curl http://localhost:8000/metrics

# Fix: Restart Prometheus
docker compose -f docker/compose.yaml restart prometheus
```

---

## Recovery Procedures

### Full System Restart

```bash
# Stop everything
docker compose -f docker/compose.yaml down
./scripts/stop_pipeline.sh

# Start fresh
docker compose -f docker/compose.yaml up -d
sleep 30
./scripts/start_pipeline.sh

# Verify
curl http://localhost:8000/health
```

### Service Recovery

```bash
# API only
docker compose -f docker/compose.yaml restart api
docker logs -f volatility-api

# Kafka only
docker compose -f docker/compose.yaml restart kafka
sleep 20
docker compose -f docker/compose.yaml restart api
./scripts/start_pipeline.sh  # Restart pipeline processes
```

### Model Rollback

```bash
# Switch to baseline model
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d api
sleep 10
curl http://localhost:8000/version  # Verify "model_variant": "baseline"

# Switch back to ML model
MODEL_VARIANT=ml docker compose -f docker/compose.yaml up -d api
```

---

## Failure Scenarios

### Scenario 1: Kafka Broker Restart

**Simulate failure:**
```bash
# Stop Kafka broker
docker compose -f docker/compose.yaml stop kafka
```

**Observe impact:**
- API health check: `curl http://localhost:8000/health` → `kafka_connected: false`
- Pipeline processes will show connection errors in logs
- Grafana will show consumer lag increasing

**Recovery steps:**
```bash
# 1. Restart Kafka
docker compose -f docker/compose.yaml start kafka

# 2. Wait for Kafka to be ready (20-30 seconds)
sleep 20

# 3. Verify Kafka is healthy
docker exec kafka kafka-broker-api-versions --bootstrap-server localhost:9092

# 4. Restart API (to reconnect)
docker compose -f docker/compose.yaml restart api

# 5. Pipeline processes auto-reconnect (check logs)
tail -f logs/featurizer_*.log
tail -f logs/predictions_*.log

# 6. Verify recovery
curl http://localhost:8000/health  # Should show kafka_connected: true
# Check Grafana - consumer lag should decrease
```

**Expected behavior:**
- Pipeline processes have exponential backoff retry logic
- They will automatically reconnect when Kafka is available
- No data loss (Kafka retains messages)
- Consumer lag will catch up once reconnected

---

### Scenario 2: API 500 Error

**Simulate failure:**
```bash
# Option 1: Temporarily break model loading
docker exec volatility-api rm /app/models/artifacts/random_forest/model.pkl
docker compose -f docker/compose.yaml restart api

# Option 2: Simulate high load causing errors
python scripts/load_test.py --requests 1000 --concurrent 50
```

**Observe impact:**
- `/predict` endpoint returns 500 errors
- Grafana shows error rate spike
- Prometheus alerts trigger (if configured)
- Prediction consumer logs show HTTP 500 responses

**Recovery steps:**
```bash
# 1. Check API logs for root cause
docker logs volatility-api | tail -50

# 2. Check health endpoint
curl http://localhost:8000/health
# If model_loaded: false, restore model file

# 3. Restore model (if deleted)
# Ensure model file exists:
ls -lh models/artifacts/random_forest/model.pkl

# 4. Restart API
docker compose -f docker/compose.yaml restart api

# 5. Wait for API to be ready
sleep 10

# 6. Verify recovery
curl http://localhost:8000/health  # Should show model_loaded: true
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'

# 7. Check metrics
curl http://localhost:8000/metrics | grep http_requests_total

# 8. Monitor Grafana dashboard
# Error rate should return to normal
# Prediction consumer will resume processing
```

**Alternative: Rollback to baseline model**
```bash
# If ML model is problematic, switch to baseline
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d api
sleep 10
curl http://localhost:8000/version  # Verify rollback
```

**Expected behavior:**
- Prediction consumer has retry logic (exponential backoff)
- Failed requests are logged but don't crash the consumer
- Once API recovers, consumer resumes processing
- Metrics show recovery in Grafana

---

## Key Metrics & Alerts

**SLOs:**
- p95 latency ≤ 800ms (aspirational)
- Error rate < 1%
- Success rate ≥ 99%

**Alert thresholds:**
- p95 Latency > 1000ms for 5 min → Investigate
- Error Rate > 2% for 5 min → Alert team
- Model Not Loaded → Immediate action
- Kafka Consumer Lag > 1000 messages → Investigate

**Prometheus queries:**
```promql
# p95 latency
histogram_quantile(0.95, rate(prediction_latency_seconds_bucket[5m])) * 1000

# Error rate
sum(rate(http_requests_total{status=~"4..|5.."}[5m])) / sum(rate(http_requests_total[5m]))

# Consumer lag
kafka_consumer_lag
```

---

**Last Updated:** November 2025  
**Version:** 2.0
