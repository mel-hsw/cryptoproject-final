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
# Start complete pipeline (all services in Docker)
docker compose -f docker/compose.yaml up -d

# Stop pipeline
docker compose -f docker/compose.yaml down
```

**What it does:**
- Builds Docker images (if needed)
- Starts all Docker services:
  - Infrastructure: Kafka (KRaft mode), MLflow
  - API: FastAPI service (exposes /predict, /health, /version, /metrics)
  - Pipeline: ingest (WebSocket), featurizer, prediction-consumer
  - Monitoring: Prometheus, Grafana
- Creates Kafka topics automatically (`ticks.raw`, `ticks.features` - 3 partitions each)
- All pipeline components run as Docker containers

**Live Pipeline Flow:**
```
Coinbase WebSocket → ingest → Kafka (ticks.raw) → 
featurizer → Kafka (ticks.features) → 
prediction-consumer → API /predict → Prometheus → Grafana
```

**Data Persistence:**
- Raw data: `data/raw/` (persisted via volume mount)
- Processed features: `data/processed/` (persisted via volume mount)
- Kafka messages: Docker volume `kafka-data`
- Prometheus metrics: Docker volume `prometheus-data`
- Grafana configs: Docker volume `grafana-data`

### Manual Setup

```bash
# 1. Start all Docker services
docker compose -f docker/compose.yaml up -d

# 2. Wait for services to be ready (about 30 seconds)
sleep 30

# 3. Verify services ready
curl http://localhost:8000/health
docker compose -f docker/compose.yaml ps  # Check all services are running
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
- Grafana: http://localhost:3000 (admin/admin123) - Real-time dashboards
- Prometheus: http://localhost:9090 - Metrics queries
- API Metrics: http://localhost:8000/metrics - Prometheus format
- MLflow: http://localhost:5001 - Experiment tracking UI

**Verify Live Pipeline:**
```bash
# 1. Check all services are running
docker compose -f docker/compose.yaml ps

# 2. Check API health
curl http://localhost:8000/health
# Expected: {"status": "healthy", "model_loaded": true, "kafka_connected": true}

# 3. Check API version (shows current model)
curl http://localhost:8000/version
# Expected: {"model": "random_forest_v1", "model_variant": "ml", ...}

# 4. Check Kafka topics exist
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092
# Expected: ticks.raw, ticks.features

# 5. Check pipeline is processing data
docker logs crypto-ingest | tail -20  # Should show WebSocket messages
docker logs crypto-featurizer | tail -20  # Should show feature computation
docker logs crypto-prediction-consumer | tail -20  # Should show API calls

# 6. Check metrics are being collected
curl http://localhost:8000/metrics | grep predictions_total
# Should show increasing counter values

# 7. View Grafana dashboards
# Open http://localhost:3000 and check:
# - Latency metrics (p50, p95)
# - Prediction rate
# - Error rate
# - Consumer lag (if configured)
```

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

# Fix: Restart Kafka + API + pipeline services
docker compose -f docker/compose.yaml restart kafka
sleep 20
docker compose -f docker/compose.yaml restart api ingest featurizer prediction-consumer
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

# Start fresh
docker compose -f docker/compose.yaml up -d

# Wait for services to be ready
sleep 30

# Verify
curl http://localhost:8000/health
docker compose -f docker/compose.yaml ps  # Check all services
```

### Service Recovery

```bash
# API only
docker compose -f docker/compose.yaml restart api
docker logs -f volatility-api

# Kafka only
docker compose -f docker/compose.yaml restart kafka
sleep 20
docker compose -f docker/compose.yaml restart api ingest featurizer prediction-consumer
```

### Model Rollback

**Understanding Model Loading:**
- Models are loaded from filesystem (`models/artifacts/`) at API startup
- MLflow is used for experiment tracking, not runtime model loading
- Model selection via environment variables: `MODEL_VARIANT` and `MODEL_VERSION`

**Rollback to Baseline Model:**
```bash
# Switch to baseline model (z-score fallback)
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d api
sleep 10
curl http://localhost:8000/version  # Verify "model_variant": "baseline"
```

**Switch to Different ML Model:**
```bash
# Use random_forest model (default)
MODEL_VARIANT=ml MODEL_VERSION=random_forest docker compose -f docker/compose.yaml up -d api
```

**Verify Model Change:**
```bash
curl http://localhost:8000/version
# Response includes: model_variant, model_path, version
```

**Note:** Model changes require API restart. The prediction-consumer will automatically reconnect and resume processing.

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

# 4. Restart API and pipeline services (to reconnect)
docker compose -f docker/compose.yaml restart api ingest featurizer prediction-consumer

# 5. Check logs to verify reconnection
docker compose -f docker/compose.yaml logs -f featurizer
docker compose -f docker/compose.yaml logs -f prediction-consumer

# 6. Verify recovery
curl http://localhost:8000/health  # Should show kafka_connected: true
# Check Grafana - consumer lag should decrease
```

**Expected behavior:**
- Pipeline services have exponential backoff retry logic
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

## Live Pipeline Monitoring

### Key Metrics to Monitor

**API Metrics (from `/metrics` endpoint):**
- `predictions_total` - Total number of predictions made
- `prediction_latency_seconds` - Prediction latency histogram
- `http_requests_total` - HTTP request counts by status
- `model_loaded` - Whether model is loaded (1) or not (0)
- `last_prediction_timestamp` - Unix timestamp of last prediction

**Pipeline Health:**
- Consumer lag (if Kafka metrics enabled)
- Feature computation rate
- Prediction rate (should match feature arrival rate)
- Error rates in each component

### Monitoring Commands

```bash
# Check prediction rate
curl -s http://localhost:8000/metrics | grep predictions_total

# Check latency
curl -s http://localhost:8000/metrics | grep prediction_latency_seconds

# Check API health
watch -n 5 'curl -s http://localhost:8000/health | jq'

# Monitor logs in real-time
docker compose -f docker/compose.yaml logs -f prediction-consumer

# Check Kafka consumer lag (if metrics enabled)
# View in Grafana dashboard or check Prometheus
```

### Troubleshooting Live Pipeline

**No Predictions Being Made:**
```bash
# 1. Check if ingest is receiving data
docker logs crypto-ingest | tail -50
# Should see WebSocket messages being received

# 2. Check if featurizer is processing
docker logs crypto-featurizer | tail -50
# Should see feature computation logs

# 3. Check if prediction-consumer is consuming
docker logs crypto-prediction-consumer | tail -50
# Should see API calls being made

# 4. Check Kafka topics have messages
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic ticks.raw --from-beginning --max-messages 5
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic ticks.features --from-beginning --max-messages 5
```

**High Latency:**
```bash
# Check API resources
docker stats volatility-api

# Check for errors
docker logs volatility-api | grep -i error

# Check prediction latency metrics
curl -s http://localhost:8000/metrics | grep prediction_latency_seconds
```

**Pipeline Not Processing:**
```bash
# Restart entire pipeline
docker compose -f docker/compose.yaml restart ingest featurizer prediction-consumer

# Check service dependencies
docker compose -f docker/compose.yaml ps
# Ensure kafka-init completed successfully
```

---

**Last Updated:** November 2025  
**Version:** 2.1
