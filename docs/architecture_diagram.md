# System Architecture Diagram

## Simple System Flow

```
Coinbase WebSocket → Ingestor → Kafka → Features → Kafka → Prediction Consumer → API → Monitoring
     (Source)      (ws_ingest) (ticks.raw) (featurizer) (ticks.features) (prediction_consumer) (FastAPI) (Prometheus/Grafana)
```

### Component Details

1. **Ingestor** (`scripts/ws_ingest.py`)
   - Streams data from Coinbase WebSocket
   - Publishes to Kafka `ticks.raw`

2. **Features** (`features/featurizer.py`)
   - Consumes from Kafka `ticks.raw`
   - Computes windowed features
   - Publishes to Kafka `ticks.features`

3. **Prediction Consumer** (`scripts/prediction_consumer.py`)
   - Consumes from Kafka `ticks.features`
   - Automatically calls `/predict` API
   - Rate-limited to 1-2 predictions/second

4. **API** (`api/app.py`)
   - FastAPI service (Port 8000)
   - `/predict` endpoint for predictions
   - `/health`, `/version`, `/metrics` endpoints

5. **Monitoring**
   - Prometheus: Collects metrics from `/metrics`
   - Grafana: Visualizes dashboards
   - MLflow: Tracks model versions