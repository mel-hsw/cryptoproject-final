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

**Narration:**
> "I'll demonstrate our real-time cryptocurrency volatility detection service. This system streams live data from Coinbase, computes features, makes ML predictions, and provides comprehensive monitoring. Let's start with a single command."

**Commands:**
```bash
cd cryptoproject-final
docker compose -f docker/compose.yaml up -d
```

**Show:**
- Docker building images (if first run)
- Services starting: kafka, mlflow, api, ingest, featurizer, prediction-consumer, prometheus, grafana
- Wait 30-60 seconds for services to be healthy

**Narration:**
> "This one command starts 11 services: Kafka for streaming, MLflow for model tracking, our FastAPI prediction service, the full data pipeline including live Coinbase ingestion, feature computation, and monitoring with Prometheus and Grafana."

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

**Narration:**
> "The health endpoint confirms our API is running, the ML model is loaded, and Kafka is connected. The version endpoint shows we're using the Random Forest model."

---

### Part 3: Live Prediction (2 min)

**Commands:**
```bash
# Make a prediction
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'
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

**Narration:**
> "The API returns a volatility probability score between 0 and 1. A score of 0.74 indicates high likelihood of a volatility spike. The prediction includes the model variant, version, and timestamp for full traceability."

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

**Narration:**
> "Grafana shows real-time performance: our p95 latency is around 50ms, well under our 800ms SLO target. We're processing live predictions with near-zero errors. MLflow tracks our production model performance with PR-AUC metrics logged every 10 minutes."

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

**Narration:**
> "Let's test fault tolerance. I'll stop Kafka to simulate a broker failure. Notice the API stays healthy and continues serving predictions. When we restart Kafka, our pipeline automatically reconnects within seconds. The featurizer logs show successful reconnection with no data loss."

---

### Part 6: Model Rollback (1 min)

**Commands:**
```bash
# Check current model
curl http://localhost:8000/version

# Rollback to baseline model
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d api

# Wait for restart (15 seconds)
sleep 15

# Verify rollback
curl http://localhost:8000/version

# Make prediction with baseline
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'

# Rollback to ML model
MODEL_VARIANT=ml docker compose -f docker/compose.yaml up -d api
```

**Expected Output:**
```json
// Before: {"model_variant":"ml",...}
// After:  {"model_variant":"baseline",...}
```

**Narration:**
> "For production safety, we can instantly rollback to a baseline statistical model if the ML model degrades. I'm setting MODEL_VARIANT to 'baseline' and restarting just the API service. The version endpoint confirms we've switched to the baseline z-score model. Predictions continue without downtime. Rolling back to the ML model is just as simple."

---

### Part 7: Wrap-Up (0.5 min)

**Commands:**
```bash
# Show all running services
docker compose -f docker/compose.yaml ps
```

**Narration:**
> "To summarize: we demonstrated one-command startup, live predictions with sub-100ms latency, real-time monitoring with Grafana and MLflow, automatic failure recovery when Kafka failed, and instant model rollback for production safety. The entire pipeline processes live cryptocurrency data end-to-end."

**Final screen:** Show Grafana dashboard with live metrics

---

## Post-Demo Cleanup

```bash
# Stop all services
docker compose -f docker/compose.yaml down
```

---

## Recording Tips

### Technical Setup
1. **Screen Resolution:** 1920x1080 (Full HD) recommended
2. **Recording Tool:**
   - macOS: QuickTime Player (Cmd+Shift+5) or OBS Studio
   - Windows: OBS Studio or built-in Xbox Game Bar
3. **Audio:** Use a decent microphone, minimize background noise
4. **Font Size:** Increase terminal font size (16-18pt) for readability

### Presentation Tips
1. **Speak Clearly:** Explain what you're doing, not just reading commands
2. **Pause for Effect:** Give viewers time to see output before moving on
3. **Highlight Key Points:**
   - "Notice the latency is only 50ms..."
   - "See how it automatically reconnected..."
4. **Stay on Script:** Practice 2-3 times to stay within 8 minutes
5. **Show Confidence:** This is your work - be proud of it!

### What to Emphasize
- ✅ **One-command startup** - shows good DevOps
- ✅ **Real-time data pipeline** - streaming from live Coinbase
- ✅ **Production-ready monitoring** - Grafana + MLflow
- ✅ **Fault tolerance** - automatic reconnection
- ✅ **Easy rollback** - environment variable toggle

### Common Mistakes to Avoid
- ❌ Don't spend too long on any one section
- ❌ Don't show errors without explaining/fixing them
- ❌ Don't read documentation - demonstrate features
- ❌ Don't go over 8 minutes (practice to stay at 7:30)
- ❌ Don't forget to explain what you're showing

---

## Troubleshooting During Recording

### If services don't start:
- Wait 60 seconds (Kafka takes time)
- Check `docker compose logs api` for errors
- Verify Docker has enough resources (8GB RAM minimum)

### If API doesn't respond:
- Check health: `docker compose ps api`
- Restart: `docker compose restart api`
- Wait 30 seconds and retry

### If Grafana doesn't load:
- Default login: admin/admin123
- Navigate to Dashboards → crypto-volatility-api
- It may take 30-60 seconds for data to appear

---

## Upload Instructions

### YouTube (Unlisted)
1. Go to https://studio.youtube.com
2. Click "Create" → "Upload video"
3. Set visibility to **"Unlisted"**
4. Title: "Real-Time Crypto AI Service - Week 7 Demo"
5. Description: "8-minute demonstration of cryptocurrency volatility detection system with live streaming, ML predictions, monitoring, and fault tolerance."
6. Copy the link for submission

### Loom (Alternative)
1. Go to https://loom.com
2. Record screen + audio
3. Share and copy link

---

## Final Checklist Before Recording

- [ ] All services stopped and volumes cleaned (`docker compose down -v`)
- [ ] Docker Desktop running with adequate resources
- [ ] Browser tabs prepared
- [ ] Terminal font size increased for readability
- [ ] Microphone tested
- [ ] Screen recording software ready
- [ ] Script reviewed and practiced
- [ ] Timer ready (aim for 7:30, max 8:00)
- [ ] Quiet recording environment

**Good luck! You've built an impressive system - now show it off!** 🚀
