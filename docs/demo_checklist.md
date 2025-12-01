# Demo Checklist

## 8-Minute Demo Script for Real-Time Crypto AI Service

**Duration:** 8 minutes  
**Format:** Screen recording with narration

---

## Pre-Demo Setup (Before Recording)

- [ ] Ensure Docker Desktop is running
- [ ] All services and pipeline stopped (`./scripts/stop_pipeline.sh` or `.\scripts\stop_pipeline.ps1`)
- [ ] Terminal windows ready
- [ ] Browser tabs ready for:
  - http://localhost:8000/docs (API Swagger)
  - http://localhost:5001 (MLflow)
  - http://localhost:3000 (Grafana)
  - http://localhost:9090 (Prometheus)

---

## Demo Script

### Part 1: System Startup (1.5 minutes)

**Narration:** "Let me show you how to start the entire end-to-end pipeline with a single command."

```bash
# One-command startup (builds images, starts services, runs pipeline)
./scripts/start_pipeline.sh
```

**Or on Windows:**
```powershell
.\scripts\start_pipeline.ps1
```

**Show:**
- [ ] One-command startup
- [ ] Docker images building (if first run)
- [ ] All services starting (Kafka, Zookeeper, MLflow, API, Prometheus, Grafana)
- [ ] Data ingestion starting (ws_ingest.py)
- [ ] Feature pipeline starting (featurizer.py)
- [ ] Prediction consumer starting (prediction_consumer.py)
- [ ] Health status becoming "healthy"

**Key Points:**
- "One command starts everything: Docker services + full pipeline"
- "The script automatically builds images if needed"
- "All components start in the correct order with dependencies"
- "Live data streaming begins immediately from Coinbase"

---

### Part 2: End-to-End Pipeline Flow (2 minutes)

**Narration:** "Let me show you the end-to-end flow: live data streaming, feature computation, and automatic predictions."

**Show:**
- [ ] Check logs showing data ingestion: `tail -f logs/ingest_*.log`
- [ ] Check logs showing feature computation: `tail -f logs/featurizer_*.log`
- [ ] Check logs showing predictions: `tail -f logs/predictions_*.log`
- [ ] Show API endpoints:
  ```bash
  curl http://localhost:8000/health
  curl http://localhost:8000/version
  ```
- [ ] Show Swagger UI at http://localhost:8000/docs

**Key Points:**
- "Live data streams from Coinbase WebSocket → Kafka"
- "Features are computed in real-time from raw ticks"
- "Prediction consumer automatically calls /predict API (1-2 predictions/second)"
- "The entire pipeline runs end-to-end without manual intervention"

---

### Part 3: Monitoring Dashboard (1.5 minutes)

**Narration:** "Let's look at our monitoring setup showing live metrics from the running pipeline."

**Show:**
- [ ] Grafana dashboard at http://localhost:3000 (no login required)
- [ ] Prediction rate graph (showing live predictions from consumer)
- [ ] Latency graph (p50/p95/p99) - showing real-time latency
- [ ] Error rate graph
- [ ] Data freshness metrics (time since last prediction)
- [ ] Prometheus at http://localhost:9090 showing live metrics

**Key Points:**
- "Live metrics show predictions happening automatically (1-2/second)"
- "We track p50, p95, and p99 latency in real-time"
- "Our SLO target is p95 under 800ms - we're achieving 91ms"
- "Data freshness shows the pipeline is running smoothly"
- "Error rate is monitored for SLO compliance"

---

### Part 4: Failure Recovery (1.5 minutes)

**Narration:** "Let me demonstrate failure recovery."

```bash
# Stop the API container to simulate failure
docker compose -f docker/compose.yaml stop api

# Show health check fails
curl http://localhost:8000/health  # Should fail

# Restart the API
docker compose -f docker/compose.yaml start api

# Wait and verify recovery
sleep 10
curl http://localhost:8000/health  # Should succeed
```

**Show:**
- [ ] API becomes unavailable
- [ ] Restart command
- [ ] System recovers automatically
- [ ] Health check passes again

**Key Points:**
- "The system can recover from failures gracefully"
- "Docker Compose handles service dependencies"

---

### Part 5: Model Rollback (1.5 minutes)

**Narration:** "Now let me show the model rollback feature."

```bash
# Check current model variant
curl http://localhost:8000/version

# Rollback to baseline model
export MODEL_VARIANT=baseline
docker compose -f docker/compose.yaml up -d api

# Verify rollback
sleep 5
curl http://localhost:8000/version  # Should show baseline

# Make prediction with baseline
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'

# Switch back to ML model
export MODEL_VARIANT=ml
docker compose -f docker/compose.yaml up -d api
```

**Show:**
- [ ] Current model is ML variant
- [ ] Switch to baseline with environment variable
- [ ] Version endpoint confirms baseline
- [ ] Predictions still work with baseline
- [ ] Switch back to ML model

**Key Points:**
- "MODEL_VARIANT toggle allows instant rollback"
- "No code changes needed - just environment variable"
- "Critical for production safety"

---

## Post-Demo Commands

```bash
# Show pipeline status
ps aux | grep -E "(ws_ingest|featurizer|prediction_consumer)"

# Show Docker services status
docker compose -f docker/compose.yaml ps

# Show load test results
python scripts/load_test.py --requests 100

# Stop pipeline
./scripts/stop_pipeline.sh

# Or on Windows
.\scripts\stop_pipeline.ps1
```

---

## Key Metrics to Mention

| Metric | Value | Notes |
|--------|-------|-------|
| p95 Latency | 91.17ms | 88.6% better than 800ms target |
| Success Rate | 100% | From load test |
| Throughput | 121.92 req/s | Handles burst traffic |
| PR-AUC (ML) | 0.9859 | 9.5x better than baseline |
| PR-AUC (Baseline) | 0.1039 | Available for rollback |

---

## Troubleshooting During Demo

### If API doesn't start:
```bash
docker compose -f docker/compose.yaml logs api
docker compose -f docker/compose.yaml restart api
```

### If Kafka is unhealthy:
```bash
docker compose -f docker/compose.yaml restart kafka
sleep 20
docker compose -f docker/compose.yaml restart api
```

### If predictions fail or pipeline stops:
```bash
# Check if pipeline processes are running
ps aux | grep -E "(ws_ingest|featurizer|prediction_consumer)"

# Check API health
curl http://localhost:8000/health

# Restart pipeline
./scripts/stop_pipeline.sh
./scripts/start_pipeline.sh
```

---

## Recording Tips

1. **Resolution:** 1920x1080
2. **Font size:** Large enough to read
3. **Speak clearly:** Explain what each command does
4. **Pause:** Let viewers see the output
5. **Highlight:** Point out key metrics and features

---

**Demo Video:** [Insert YouTube/Loom link here after recording]

**Last Updated:** December 1, 2025

