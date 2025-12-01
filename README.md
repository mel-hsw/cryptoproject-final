# Real-Time Crypto AI Service

Real-time cryptocurrency volatility detection service that streams Coinbase data, computes features, and makes ML predictions via FastAPI.

---

# Quickstart

## macOS/Linux 
```
git clone <repository-url> && cd Crypto-Project-FOAI_edited-mel
./setup_python.sh  # Sets up Python 3.9.25
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
 python ./scripts/start_pipeline.sh
curl http://localhost:8000/health
```

## Windows (PowerShell)
```
git clone <repository-url>; cd Crypto-Project-FOAI_edited-mel
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\scripts\start_pipeline.ps1
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

API Metrics (Prometheus): http://localhost:8000/metrics
Monitoring Dashboards (Grafana): http://localhost:3000
Metrics Database (Prometheus): http://localhost:9090
Model Tracking (MLflow): http://localhost:5001

---

# Prerequisites

- **Docker Desktop** (v20.10+)
- **Python 3.9.25** (specified in `.python-version`)
- **Git**

**System:** macOS/Linux/Windows (WSL2 recommended), 8GB+ RAM, 10GB disk

---

**Key Directories:**

- **`api/`** - FastAPI service exposing `/predict`, `/health`, `/metrics` endpoints
- **`scripts/`** - Pipeline orchestration and data ingestion scripts
- **`features/`** - Real-time feature engineering from raw ticks
- **`models/`** - ML model training, inference, and artifacts
- **`docker/`** - Containerized services (Kafka, API, monitoring)
- **`docs/`** - Operational documentation and performance reports
- **`data/`** - Raw and processed data (not committed to git)
- **`logs/`** - Application logs (not committed to git)

---
# Setup (Detailed)

## macOS/Linux

```bash
# Clone repository
git clone <repository-url>
cd Crypto-Project-FOAI_edited-mel

# Set up Python 3.9.25 (if not already done)
./setup_python.sh  # Or follow manual setup instructions above

# Create virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start services
chmod +x scripts/start_pipeline.sh
./scripts/start_pipeline.sh
```

## Windows (PowerShell)

```powershell
# Clone repository
git clone <repository-url>
cd Crypto-Project-FOAI_edited-mel

# Set up Python environment
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Start services
.\scripts\start_pipeline.ps1
```

**What this does:**
- Builds Docker images (if needed)
- Starts all services (Kafka, API, Prometheus, Grafana, MLflow)
- Creates Kafka topics
- Starts data ingestion and feature pipeline

---

# Testing the API

## Health Check

**macOS/Linux:**
```bash
curl http://localhost:8000/health
```

**Windows (PowerShell):**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

## Make Predictions

**macOS/Linux:**
```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'
```

**Windows (PowerShell):**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/predict" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"rows":[{"ret_mean":0.05,"ret_std":0.01,"n":50}]}'
```

**Expected Response:**
```json
{
  "scores": [0.74],
  "model_variant": "ml",
  "version": "v1.2-random_forest",
  "ts": "2025-11-25T18:53:40Z"
}
```

---

# Monitoring

- **Grafana:** http://localhost:3000 (admin/admin123) - Real-time dashboards (latency p50/p95, error rate, freshness)
- **Prometheus:** http://localhost:9090 - Metrics queries
- **API Metrics:** `curl http://localhost:8000/metrics` (macOS/Linux) or `Invoke-WebRequest http://localhost:8000/metrics` (Windows)

---

# Stopping

**macOS/Linux:**
```bash
./scripts/stop_pipeline.sh
```

**Windows (PowerShell):**
```powershell
.\scripts\stop_pipeline.ps1
```

---

# Model Rollback

**macOS/Linux:**
```bash
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d api
MODEL_VARIANT=ml docker compose -f docker/compose.yaml up -d api  # Switch back
```

**Windows (PowerShell):**
```powershell
$env:MODEL_VARIANT="baseline"
docker compose -f docker/compose.yaml up -d api

$env:MODEL_VARIANT="ml"  # Switch back
docker compose -f docker/compose.yaml up -d api
```

**Verify:**
```bash
curl http://localhost:8000/version  # macOS/Linux
Invoke-RestMethod http://localhost:8000/version  # Windows
```

---

# Troubleshooting

**API 503:** Check model exists, restart: `docker compose restart api`

**Kafka Connection:** Restart Kafka: `docker compose restart kafka`

**No Data:** Test WebSocket: `python scripts/ws_ingest.py --pair BTC-USD --save-disk`

**High Latency:** Check resources: `docker stats volatility-api`

**Port Conflicts:**
- **macOS/Linux:** `lsof -i :8000`
- **Windows:** `netstat -ano | findstr :8000`

See `docs/runbook.md` for detailed troubleshooting.

---

# Additional Resources

- **Runbook:** `docs/runbook.md` - Operations guide
- **SLOs:** `docs/slo.md` - Service level objectives (p95 ≤ 800ms)
- **Performance:** `docs/performance_summary.md` - Benchmarks
