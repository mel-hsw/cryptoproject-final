# Real-Time Crypto AI Service

Real-time cryptocurrency volatility detection service that streams Coinbase data, computes features, and makes ML predictions via FastAPI.

---

# Quickstart

## macOS/Linux 
```bash
git clone <repository-url> && cd cryptoproject-final
docker compose -f docker/compose.yaml up -d
curl http://localhost:8000/health
```

## Windows (PowerShell)
```powershell
git clone <repository-url>; cd cryptoproject-final
docker compose -f docker/compose.yaml up -d
Invoke-RestMethod -Uri "http://localhost:8000/health"
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

**Key Directories:**

- **`api/`** - FastAPI service exposing `/predict`, `/health`, `/version`, `/metrics` endpoints
- **`scripts/`** - Pipeline orchestration and data ingestion scripts
- **`features/`** - Real-time feature engineering from raw ticks
- **`models/`** - ML model training, inference, and artifacts
- **`docker/`** - Containerized services (Kafka, API, monitoring)
- **`docs/`** - Operational documentation and performance reports
- **`data/`** - Raw and processed data (not committed to git)
- **`logs/`** - Application logs (not committed to git)

---

# Architecture Overview

## System Flow

```
Coinbase WebSocket → Ingest Container → Kafka (ticks.raw) → 
Featurizer Container → Kafka (ticks.features) → 
Prediction Consumer → FastAPI /predict → Prometheus → Grafana
```

## Components

### Data Ingestion
- **Ingest Container** (`scripts/ws_ingest.py`)
  - Connects to Coinbase Advanced Trade WebSocket
  - Streams real-time ticker data
  - Publishes to Kafka `ticks.raw` topic
  - Optionally saves raw data to `data/raw/` (persisted via volume)

### Feature Engineering
- **Featurizer Container** (`features/featurizer.py`)
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

### Model Management
- **MLflow** (Port 5001)
  - Experiment tracking during training
  - Model registry and versioning
  - Metrics comparison and visualization
  - **Note:** Models are served from filesystem, not directly from MLflow

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
git clone <repository-url>
cd cryptoproject-final

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
git clone <repository-url>
cd cryptoproject-final

# Start all services (builds images if needed)
docker compose -f docker/compose.yaml up -d

# Wait for services to be ready (about 30 seconds)
Start-Sleep -Seconds 30

# Verify API is healthy
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

**What this does:**
- Builds Docker images (if needed)
- Starts all services (Kafka, Zookeeper, MLflow, API, Prometheus, Grafana)
- Creates Kafka topics automatically (via kafka-init service)
- Starts pipeline components (ingest, featurizer, prediction-consumer)

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

This system uses **MLflow for experiment tracking** during model training, while **model artifacts are stored on the filesystem** for production serving. This separation provides flexibility for version control, rollback, and production deployments.

## Model Lifecycle Flow

```
Training → MLflow (logs metrics/params) → Save to filesystem → API loads from filesystem
```

### 1. Training Phase
- Models are trained using `models/train.py`
- **MLflow tracks:**
  - Model parameters (hyperparameters, feature sets)
  - Training metrics (PR-AUC, F1, precision, recall)
  - Model artifacts (saved to MLflow artifact store)
  - Experiment metadata (git SHA, timestamps)

### 2. Model Storage
- Trained models are saved to `models/artifacts/{model_name}/model.pkl`
- MLflow logs reference to these artifacts
- Models are versioned by directory structure:
  ```
  models/artifacts/
  ├── baseline/
  │   └── model.pkl
  ├── logistic_regression/
  │   └── model.pkl
  └── random_forest/
      └── model.pkl
  ```

### 3. Production Serving
- **API loads models from filesystem** at startup (not directly from MLflow)
- Model selection via environment variables:
  - `MODEL_VARIANT`: `ml` (trained model) or `baseline` (z-score fallback)
  - `MODEL_VERSION`: `random_forest`, `logistic_regression`, etc.
- Models are loaded once at startup for performance

## Why This Architecture?

**Separation of Concerns:**
- **MLflow** = Experiment tracking, comparison, and model registry
- **Filesystem** = Production model storage and versioning
- **API** = Fast model loading and serving

**Benefits:**
- ✅ Fast startup (no MLflow API calls during serving)
- ✅ Version control via filesystem (git-friendly)
- ✅ Easy rollback (change `MODEL_VARIANT` env var)
- ✅ MLflow UI for experiment comparison
- ✅ Production isolation (API doesn't depend on MLflow availability)

## MLflow Access

- **MLflow UI:** http://localhost:5001
- **View experiments:** Navigate to "crypto-volatility-detection" experiment
- **Compare runs:** Use MLflow UI to compare different model versions
- **Model registry:** Models are tracked but served from filesystem

## Model Versioning

Models are versioned using:
1. **Directory structure:** `models/artifacts/{model_name}/`
2. **Environment variables:** `MODEL_VARIANT` and `MODEL_VERSION`
3. **Git commits:** Model files can be committed for version control

**To switch models:**
```bash
# Use random_forest model
MODEL_VARIANT=ml MODEL_VERSION=random_forest docker compose -f docker/compose.yaml up -d api

# Use logistic_regression model
MODEL_VARIANT=ml MODEL_VERSION=logistic_regression docker compose -f docker/compose.yaml up -d api

# Use baseline (z-score) model
MODEL_VARIANT=baseline docker compose -f docker/compose.yaml up -d api
```

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

See `docs/runbook.md` for detailed troubleshooting.

---

# Model Registry & Comparison

## Register Models in MLflow

Register existing models from the artifacts folder:

```bash
# Register all models (baseline + random_forest)
python scripts/register_models_mlflow.py

# Register specific model
python scripts/register_models_mlflow.py --model baseline
python scripts/register_models_mlflow.py --model random_forest
```

**View registered models:** http://localhost:5001 → Models → volatility-detector

## Enable Prediction Logging

Enable prediction logging for model comparison:

```bash
LOG_PREDICTIONS=true docker compose -f docker/compose.yaml up -d prediction-consumer
```

Predictions are logged to:
- Kafka topic: `predictions.log`
- File fallback: `logs/predictions/` (if Kafka unavailable)

## Model Comparison Dashboard

View ML vs Baseline comparison in Grafana:
- **Grafana:** http://localhost:3000 → "Model Comparison: ML vs Baseline" section
- Shows prediction scores, rates, and averages for both models

**See:** `docs/model_registry_guide.md` for detailed instructions

---

# Additional Resources

- **Runbook:** `docs/runbook.md` - Operations guide
- **SLOs:** `docs/slo.md` - Service level objectives (p95 ≤ 800ms)
- **Performance:** `docs/performance_summary.md` - Benchmarks
- **Model Registry:** `docs/model_registry_guide.md` - MLflow model registry and comparison guide
