# Demo Preparation Guide - Week 7

**Target Duration:** 8 minutes
**Format:** Screen recording with narration (YouTube or unlisted Loom)
**Required Elements:** Startup, prediction, failure recovery, rollback

---

## Pre-Demo Setup (Do This Before Recording)

### 1. Clean Slate
```bash
# Stop all services and clean volumes
docker compose -f docker/compose.yaml down -v

# Verify Docker Desktop is running
docker ps
```

### 2. Prepare Browser Tabs
Open these URLs in separate tabs (will show after startup):
- http://localhost:8000/docs (FastAPI Swagger UI)
- http://localhost:3000 (Grafana - login: admin/admin123)
- http://localhost:5001 (MLflow)
- http://localhost:9090 (Prometheus - optional)

### 3. Prepare Terminal Windows
- **Terminal 1:** Main demo terminal (commands)
- **Terminal 2:** Docker logs monitoring (optional)

### 4. Test Run
Do a complete practice run to ensure:
- All services start successfully
- Grafana dashboard loads
- API responds to predictions
- Rollback works

---

## 8-Minute Demo Script

### Part 1: System Startup (1 min)

**Quick Start:**
```bash
cd cryptoproject-final
docker compose -f docker/compose.yaml up -d
```

**Show docker logs:**
```bash
docker compose -f docker/compose.yaml logs -f
```

**Show:**
- Docker building images (if first run)
- Services starting: kafka, mlflow, api, ingest, featurizer, prediction-consumer, prometheus, grafana
- Wait 30-60 seconds for services to be healthy


---

### Part 2: Verify System Health (1 min)

**Commands:**
```bash
# Check API health
curl http://localhost:8000/health

# Check model version
curl http://localhost:8000/version
```

**Expected Output:**
```json
{"status":"healthy","model_loaded":true,"kafka_connected":true}
{"model_variant":"ml","version":"v1.2-random_forest","sha":"abc123"}
```

---

### Part 3: Live Prediction (2 min)

**Commands:**
```bash
# Make a prediction with actual features
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50,"log_return_300s":0.001,"spread_mean_300s":0.5,"trade_intensity_300s":100,"order_book_imbalance_300s":0.6,"spread_mean_60s":0.3,"order_book_imbalance_60s":0.55,"price_velocity_300s":0.0001,"realized_volatility_300s":0.002,"order_book_imbalance_30s":0.52,"realized_volatility_60s":0.0015}]}'
```

**Expected Output:**
```json
{
  "scores": [0.74],
  "model_variant": "ml",
  "version": "v1.2-random_forest",
  "ts": "2025-12-06T18:53:40Z"
}
```

**Show in Browser:**
1. Open http://localhost:8000/docs
2. Navigate to POST /predict
3. Try interactive prediction with different inputs
4. Show response format matches contract
---

### Part 4: Monitoring Dashboards (1.5 min)

**Show Grafana:** http://localhost:3000
1. Navigate to crypto-volatility dashboard
2. Point out key metrics:
   - **Latency:** p50, p95 (should be under 100ms)
   - **Request count:** Growing as predictions flow
   - **Error rate:** Should be near 0%
   - **Data freshness:** Real-time updates

**Show MLflow:** http://localhost:5001
1. Navigate to "crypto-volatility-production" experiment
2. Show PR-AUC metrics over time
3. Point out production monitoring

---

### Part 5: Failure Recovery (1 min)

**Commands:**
```bash
# Simulate Kafka failure
docker compose -f docker/compose.yaml stop kafka

# Wait 5 seconds
sleep 5

# Check API health (should still respond)
curl http://localhost:8000/health

# Restart Kafka
docker compose -f docker/compose.yaml start kafka

# Wait for reconnection (10 seconds)
sleep 10

# Verify pipeline recovered
docker compose -f docker/compose.yaml logs --tail=20 featurizer
```
---

### Part 6: Model Rollback (1 min)

**Commands:**
```bash
# Check current model
curl http://localhost:8000/version

# Rollback to baseline model
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d --force-recreate api

# Wait for restart (15 seconds)
sleep 15

# Verify rollback
curl http://localhost:8000/version

# Make prediction with baseline
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50,"log_return_300s":0.001,"spread_mean_300s":0.5,"trade_intensity_300s":100,"order_book_imbalance_300s":0.6,"spread_mean_60s":0.3,"order_book_imbalance_60s":0.55,"price_velocity_300s":0.0001,"realized_volatility_300s":0.002,"order_book_imbalance_30s":0.52,"realized_volatility_60s":0.0015}]}'

# Rollback to ML model
MODEL_VARIANT=ml docker compose -f docker/compose.yaml up -d --force-recreate api
```
---
## Post-Demo Cleanup

```bash
# Stop all services
docker compose -f docker/compose.yaml down
```

---

