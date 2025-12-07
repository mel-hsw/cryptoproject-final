# Real-Time Crypto AI Service

Real-time cryptocurrency volatility detection service that streams Coinbase data,  computes features in real-time, makes ML predictions, and provides comprehensive monitoring—all with production-grade resilience and safety mechanisms.

**Name**: Asli Gulcur, Melissa Wong (Group 9)

**[Demo Video](https://youtu.be/AZkts6N6DJ4)**

---

# Quickstart

## macOS/Linux 
```bash
git clone https://github.com/mel-hsw/cryptoproject-final && cd cryptoproject-final-main
docker compose -f docker/compose.yaml up -d
```

## Windows (PowerShell)
```powershell
git clone https://github.com/mel-hsw/cryptoproject-final; cd cryptoproject-final-main
docker compose -f docker/compose.yaml up -d
```

API Metrics (Prometheus): http://localhost:8000/metrics
Monitoring Dashboards (Grafana): http://localhost:3000/d/crypto-volatility-api/crypto-volatility-detection-api 
Model Tracking (MLflow): http://localhost:5001

---

# Prerequisites

- **Docker Desktop** (v20.10+)
- **Python 3.9.25** (specified in `.python-version`)
- **Git**

**System:** macOS/Linux/Windows (WSL2 recommended), 8GB+ RAM, 10GB disk

---

# Configuration Management

## Environment Variables

The system uses optional environment variables for configuration across all services. This approach:
- **Prevents configuration mistakes** by providing a centralized template
- **Reduces secret leakage risk** by keeping sensitive values out of version control
- **Simplifies deployment** by providing environment-specific configurations

### Quick Setup

1. **Copy the template:**
   ```bash
   cp .env.example .env
   ```

2. **Customize (optional):** The default values are production-ready, but you can customize:
   - Model variant: `MODEL_VARIANT=ml` or `MODEL_VARIANT=baseline`
   - Model version: `MODEL_VERSION=random_forest`
   - Log level: `LOG_LEVEL=INFO` or `LOG_LEVEL=DEBUG`
   - Kafka settings: brokers, topics, consumer timeouts

3. **Run services:** Docker Compose automatically loads `.env`:
   ```bash
   docker compose -f docker/compose.yaml up -d
   ```

### Key Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_VARIANT` | `ml` | Model type: `ml` (trained) or `baseline` (z-score) |
| `MODEL_VERSION` | `random_forest` | Model version when using `ml` variant |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:29092` | Kafka broker addresses |
| `MLFLOW_TRACKING_URI` | `http://mlflow:5000` | MLflow tracking server URI |
| `LOG_LEVEL` | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |

See [.env.example](.env.example) for the complete list of configuration options.

### Security Notes

- ✅ `.env` is in `.gitignore` - never committed to version control
- ✅ `.env.example` provides a safe template without secrets
- ✅ This project uses public Coinbase market data (no API keys required)
- ⚠️ For production deployments, use secret management services (AWS Secrets Manager, HashiCorp Vault, etc.)

---

**Key Directories:**

- **`api/`** - FastAPI service exposing `/predict`, `/health`, `/version`, `/metrics` endpoints
- **`scripts/`** - Pipeline orchestration, data ingestion, and feature engineering
- **`models/`** - ML model feature preparation and artifacts
- **`docker/`** - Containerized services (Kafka, API, monitoring)
- **`docs/`** - Operational documentation and performance reports
- **`data/`** - Raw and processed data (persisted via Docker volumes)
- **`logs/`** - Application logs (persisted via Docker volumes)

---

# Architecture Overview

## System Flow

```
Coinbase WebSocket → Ingest Container → Kafka (ticks.raw) →
Featurizer Container → Kafka (ticks.features) →
Prediction Consumer → FastAPI /predict → Prometheus → Grafana
                                  ↓
                        PR-AUC Monitor → MLflow (production metrics)
```

## Components

### Data Ingestion
- **Ingest Container** (`scripts/ws_ingest.py`)
  - Connects to Coinbase Advanced Trade WebSocket
  - Streams real-time ticker data
  - Publishes to Kafka `ticks.raw` topic
  - Optionally saves raw data to `data/raw/` (persisted via volume)

### Feature Engineering
- **Featurizer Container** (`scripts/featurizer.py`)
  - Consumes from Kafka `ticks.raw`
  - Computes windowed features (30s, 60s, 300s windows)
  - Publishes features to Kafka `ticks.features` topic
  - Saves processed features to `data/processed/` (persisted via volume)

### Prediction Pipeline
- **Prediction Consumer** (`scripts/prediction_consumer.py`)
  - Consumes features from Kafka `ticks.features`
  - Formats features according to API contract
  - Calls `/predict` API endpoint automatically
  - Rate-limited to prevent API overload

### API Service
- **FastAPI Container** (`api/app.py`)
  - **Endpoints:**
    - `POST /predict` - Make volatility predictions
    - `GET /health` - Health check (model loaded, Kafka connected)
    - `GET /version` - Model version and metadata
    - `GET /metrics` - Prometheus metrics
  - Loads models from filesystem at startup
  - Exposes Prometheus metrics for monitoring

### Model Management & Monitoring
- **MLflow** (Port 5001)
  - **Production monitoring only** (training scripts removed for submission)
  - PR-AUC tracking over time (via pr-auc-monitor service)
  - Time-series visualization of production metrics
  - **Note:** Models are pre-trained and served from filesystem

- **PR-AUC Monitor** (`scripts/monitor_pr_auc.py`)
  - Continuously monitors model performance on live predictions
  - Compares predictions with labels from featurizer
  - Logs metrics to MLflow every 10 minutes for time-series visualization
  - Access charts at http://localhost:5001

### Observability
- **Prometheus** (Port 9090)
  - Scrapes metrics from API `/metrics` endpoint
  - Stores time-series metrics
  - Provides query interface for metrics
- **Grafana** (Port 3000)
  - Visualizes Prometheus metrics
  - Real-time dashboards (latency, error rate, predictions)
  - Pre-configured dashboards for monitoring

### Infrastructure
- **Kafka** (KRaft mode, Port 9092)
  - Event streaming platform
  - Topics: `ticks.raw`, `ticks.features`
  - No Zookeeper required (KRaft mode)
- **Kafka Init** (one-time service)
  - Creates Kafka topics on startup
  - Ensures topics exist before pipeline starts

## Data Persistence

- **Raw Data:** `data/raw/` - Persisted via Docker volume mount
- **Processed Features:** `data/processed/` - Persisted via Docker volume mount
- **Model Artifacts:** `models/artifacts/` - Read-only mount in API container
- **MLflow Data:** `docker/mlflow_data/` - Persisted for experiment tracking
- **Kafka Data:** Docker volume `kafka-data` - Retains Kafka messages
- **Prometheus Data:** Docker volume `prometheus-data` - Retains metrics history
- **Grafana Data:** Docker volume `grafana-data` - Retains dashboard configs

## Service Dependencies

```
kafka (health check) → kafka-init (completes) → ingest, featurizer, prediction-consumer
kafka (health check) → api (health check)
mlflow (started) → api (depends on)
api (health check) → prometheus (depends on)
prometheus (health check) → grafana (depends on)
```

---
# Setup (Detailed)

## macOS/Linux

```bash
# Clone repository
git clone https://github.com/mel-hsw/cryptoproject-final
cd cryptoproject-final-main

# Start all services (builds images if needed)
docker compose -f docker/compose.yaml up -d

# Wait for services to be ready (about 30 seconds)
sleep 30

# Verify API is healthy
curl http://localhost:8000/health
```

## Windows (PowerShell)

```powershell
# Clone repository
git clone https://github.com/mel-hsw/cryptoproject-final
cd cryptoproject-final-main

# Start all services (builds images if needed)
docker compose -f docker/compose.yaml up -d

# Wait for services to be ready (about 30 seconds)
Start-Sleep -Seconds 30

# Verify API is healthy
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

**What this does:**
- Builds Docker images (if needed)
- Starts all services:
  - **Infrastructure:** Kafka (KRaft mode - no Zookeeper), MLflow
  - **API:** FastAPI service
  - **Pipeline:** ingest, featurizer, prediction-consumer, pr-auc-monitor
  - **Monitoring:** Prometheus, Grafana
- Creates Kafka topics automatically (via kafka-init service)
- All pipeline components run as Docker containers

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
docker compose -f docker/compose.yaml down
```

**Windows (PowerShell):**
```powershell
docker compose -f docker/compose.yaml down
```

**Note:** This stops all services. To stop and remove volumes (clean slate):
```bash
docker compose -f docker/compose.yaml down -v
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

# Model Lifecycle & MLflow

## Overview

This system uses **pre-trained models stored on the filesystem** for production serving, with **MLflow used exclusively for monitoring production predictions**. Training scripts have been deliberately removed for this project submission - models were trained offline and committed as artifacts.

## Model Lifecycle Flow

```
Pre-trained Models (filesystem) → API loads models → Production predictions → MLflow (logs metrics)
```

**Note:** Training scripts have been deliberately removed for this project submission. Models were trained offline and committed to the repository as artifacts.

### 1. Model Storage
- Pre-trained models are stored in `models/artifacts/{model_name}/model.pkl`
- Models are versioned by directory structure:
  ```
  models/artifacts/
  ├── baseline/
  │   └── model.pkl                      # Z-score baseline model
  └── random_forest/
      ├── model.pkl                       # Random Forest model
      ├── threshold_metadata.json         # Optimized threshold (0.034)
      └── evaluation_metrics.json         # Training metrics
  ```

### 2. Production Serving
- **API loads models from filesystem** at startup
- Model selection via environment variables:
  - `MODEL_VARIANT`: `ml` (trained model) or `baseline` (z-score fallback)
  - `MODEL_VERSION`: `random_forest`, `logistic_regression`, etc.
- Models are loaded once at startup for performance

### 3. Production Monitoring with MLflow
- **MLflow is used exclusively for monitoring production predictions** (not for training)
- **PR-AUC Monitor** (`scripts/monitor_pr_auc.py`) continuously logs production metrics:
  - PR-AUC, precision, recall, F1 score
  - True positives, false positives, true negatives, false negatives
  - Time-series visualization at http://localhost:5001
- Metrics are logged every 10 minutes for ongoing performance tracking

## Why This Architecture?

**Separation of Concerns:**
- **Filesystem** = Model storage and versioning (pre-trained models)
- **API** = Fast model loading and serving
- **MLflow** = Production monitoring and metrics tracking

**Benefits:**
- ✅ Fast startup (no training or MLflow API calls during serving)
- ✅ Version control via filesystem (git-friendly)
- ✅ Easy rollback (change `MODEL_VARIANT` env var)
- ✅ MLflow UI for production metrics visualization
- ✅ Production isolation (API doesn't depend on MLflow for serving)

## MLflow Access

- **MLflow UI:** http://localhost:5001
- **View production metrics:** Navigate to "crypto-volatility-production" experiment
- **Time-series charts:** View PR-AUC, precision, recall, and F1 score over time
- **Note:** MLflow is used for production monitoring only (training scripts removed)

## Model Versioning

Models are versioned using:
1. **Directory structure:** `models/artifacts/{model_name}/`
2. **Environment variables:** `MODEL_VARIANT` and `MODEL_VERSION`
3. **Git commits:** Model files can be committed for version control

**To switch models:**
```bash
# Use random_forest model (default ML model)
MODEL_VARIANT=ml MODEL_VERSION=random_forest docker compose -f docker/compose.yaml up -d api

# Use baseline (z-score) model (fallback)
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d api
```

**Available Models:**
- `random_forest` - Primary ML model (optimized threshold: 0.034)
- `baseline` - Z-score statistical model (fallback)

**Check current model:**
```bash
curl http://localhost:8000/version
# Response includes: model_variant, model_path, version
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

See [docs/w6_runbook.md](docs/w6_runbook.md) for detailed troubleshooting and operations guide.

---

# Additional Resources

- **[Runbook](docs/w6_runbook.md)** - Complete operations guide (startup, health checks, troubleshooting)
- **[Performance Summary](docs/w6_performance_summary.md)** - Latency benchmarks, uptime, PR-AUC metrics
- **[Data Drift Summary](docs/w6_drift_summary.md)** - Drift detection reports and analysis
- **[SLO Definition](docs/w6_slo.md)** - Service level objectives (p95 ≤ 800ms target)
- **[Architecture Rationale](docs/w4_selection_rationale.md)** - Design decisions and trade-offs
- **[Team Charter](docs/w4_team_charter.md)** - Project scope and responsibilities
