"""
FastAPI application for real-time volatility prediction.
Provides /health, /predict, /version, and /metrics endpoints.
"""

import os
import time
import logging
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Import inference logic
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.infer import (  # noqa: E402
    VolatilityPredictor,
    prepare_features_for_inference,
)

# Setup structured logging with JSON format
try:
    from pythonjsonlogger import jsonlogger

    # Configure JSON logger
    logHandler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    logHandler.setFormatter(formatter)

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.addHandler(logHandler)
    logger.propagate = False
except ImportError:
    # Fallback to standard logging if json logger not available
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

# Prometheus metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status"],
)

prediction_latency_seconds = Histogram(
    "prediction_latency_seconds",
    "Prediction latency in seconds",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

predictions_total = Counter(
    "predictions_total", "Total number of predictions", ["model_version", "prediction"]
)

model_loaded = Gauge(
    "model_loaded", "Whether the model is loaded (1) or not (0)", ["model_version"]
)

# Real-time feature metrics for dashboard visualization
feature_value = Gauge(
    "feature_value",
    "Real-time feature values from predictions",
    ["feature_name"],
)

prediction_score = Gauge(
    "prediction_score",
    "Most recent prediction score",
    ["model_variant"],
)

avg_prediction_score = Gauge(
    "avg_prediction_score",
    "Rolling average prediction score (last 100 predictions)",
    ["model_variant"],
)

# Timestamp of last prediction for freshness tracking
last_prediction_timestamp = Gauge(
    "last_prediction_timestamp",
    "Unix timestamp of the last prediction made",
)

# Store recent scores for rolling average
_recent_scores: list = []
_MAX_RECENT_SCORES = 100

# System metrics for hardware monitoring
system_cpu_percent = Gauge(
    "system_cpu_percent",
    "System CPU usage percentage",
)

system_memory_percent = Gauge(
    "system_memory_percent",
    "System memory usage percentage",
)

system_memory_available_gb = Gauge(
    "system_memory_available_gb",
    "System memory available in GB",
)

# Try to import psutil for system metrics
try:
    import psutil as _psutil

    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    logger.warning("psutil not installed - system metrics disabled")

# Initialize FastAPI app
app = FastAPI(
    title="Crypto Volatility Detection API",
    description="Real-time volatility spike prediction API",
    version="1.0.0",
)

# Initialize rate limiter (generous limits for demo - 1000/min per IP)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Global model predictor
predictor: Optional[VolatilityPredictor] = None
MODEL_VARIANT = os.getenv("MODEL_VARIANT", "ml")  # 'ml' or 'baseline'
MODEL_VERSION = os.getenv("MODEL_VERSION", "random_forest")
MODEL_PATH = os.getenv(
    "MODEL_PATH",
    str(
        Path(__file__).parent.parent
        / "models"
        / "artifacts"
        / MODEL_VERSION
        / "model.pkl"
    ),
)
BASELINE_MODEL_PATH = os.getenv(
    "BASELINE_MODEL_PATH",
    str(
        Path(__file__).parent.parent / "models" / "artifacts" / "baseline" / "model.pkl"
    ),
)


# Request/Response models - Assignment API Contract
class FeatureRow(BaseModel):
    """Single row of features for prediction."""

    # Assignment-compatible feature names (simple)
    ret_mean: Optional[float] = Field(None, description="Return mean")
    ret_std: Optional[float] = Field(None, description="Return standard deviation")
    n: Optional[int] = Field(None, description="Sample count")

    # Full feature names (internal model features)
    log_return_300s: Optional[float] = Field(None, description="Log return over 300s")
    spread_mean_300s: Optional[float] = Field(None, description="Mean spread over 300s")
    trade_intensity_300s: Optional[float] = Field(
        None, description="Trade intensity over 300s"
    )
    order_book_imbalance_300s: Optional[float] = Field(
        None, description="Order book imbalance over 300s"
    )
    spread_mean_60s: Optional[float] = Field(None, description="Mean spread over 60s")
    order_book_imbalance_60s: Optional[float] = Field(
        None, description="Order book imbalance over 60s"
    )
    price_velocity_300s: Optional[float] = Field(
        None, description="Price velocity over 300s"
    )
    realized_volatility_300s: Optional[float] = Field(
        None, description="Realized volatility over 300s"
    )
    order_book_imbalance_30s: Optional[float] = Field(
        None, description="Order book imbalance over 30s"
    )
    realized_volatility_60s: Optional[float] = Field(
        None, description="Realized volatility over 60s"
    )

    class Config:
        extra = "allow"  # Allow additional fields


class PredictRequest(BaseModel):
    """Prediction request - Assignment API Contract."""

    rows: list[FeatureRow] = Field(
        ...,
        description="List of feature rows for batch prediction",
        example=[{"ret_mean": 0.05, "ret_std": 0.01, "n": 50}],
    )


class PredictResponse(BaseModel):
    """Prediction response - Assignment API Contract."""

    scores: list[float] = Field(
        ..., description="Prediction probabilities for each row"
    )
    model_variant: str = Field(..., description="Model variant used (ml or baseline)")
    version: str = Field(..., description="Model version")
    ts: str = Field(..., description="Timestamp of prediction")


# Legacy request/response models (backward compatibility)
class FeatureRequest(BaseModel):
    """
    Legacy model - expects 10 features. Missing features will be filled with 0.0.
    """

    features: Dict[str, float] = Field(
        ...,
        description="Dictionary of feature values. Missing features will be filled with 0.0.",
        example={
            "log_return_300s": 0.001,
            "spread_mean_300s": 0.5,
            "trade_intensity_300s": 100,
            "order_book_imbalance_300s": 0.6,
            "spread_mean_60s": 0.3,
            "order_book_imbalance_60s": 0.55,
            "price_velocity_300s": 0.0001,
            "realized_volatility_300s": 0.002,
            "order_book_imbalance_30s": 0.52,
            "realized_volatility_60s": 0.0015,
        },
    )


class PredictionResponse(BaseModel):
    """Legacy prediction response."""

    prediction: int = Field(..., description="Binary prediction (0=normal, 1=spike)")
    probability: float = Field(..., description="Prediction probability [0, 1]")
    alert: bool = Field(..., description="Whether to alert (prediction == 1)")
    inference_time_ms: float = Field(..., description="Inference time in milliseconds")
    model_version: str = Field(..., description="Model version used")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Service status")
    timestamp: str = Field(..., description="Current timestamp")
    model_loaded: bool = Field(..., description="Whether model is loaded")
    kafka_connected: bool = Field(..., description="Whether Kafka is connected")


class VersionResponse(BaseModel):
    """Version information - Assignment API Contract."""

    model: str = Field(..., description="Model name (e.g., rf_v1)")
    sha: str = Field(..., description="Git commit SHA or version identifier")
    version: str = Field(..., description="API version")
    model_variant: str = Field(..., description="Model variant (ml or baseline)")
    model_path: str = Field(..., description="Path to model file")


def update_system_metrics():
    """Update system hardware metrics for monitoring."""
    if _HAS_PSUTIL:
        try:
            system_cpu_percent.set(_psutil.cpu_percent(interval=None))
            mem = _psutil.virtual_memory()
            system_memory_percent.set(mem.percent)
            system_memory_available_gb.set(round(mem.available / (1024**3), 2))
        except Exception as e:
            logger.debug(f"Failed to update system metrics: {e}")


def load_model():
    """Load the prediction model based on MODEL_VARIANT."""
    global predictor

    # Determine which model to load based on MODEL_VARIANT
    if MODEL_VARIANT == "baseline":
        model_path = BASELINE_MODEL_PATH
        model_name = "baseline"
    else:
        model_path = MODEL_PATH
        model_name = MODEL_VERSION

    if predictor is not None:
        logger.info(f"Model already loaded: {model_name}")
        return

    try:
        logger.info(
            "loading_model",
            extra={
                "model_variant": MODEL_VARIANT,
                "model_path": model_path,
                "model_name": model_name,
            },
        )
        if not Path(model_path).exists():
            raise FileNotFoundError(f"Model not found at {model_path}")

        predictor = VolatilityPredictor(model_path)
        model_loaded.labels(model_version=model_name).set(1)
        logger.info(
            "model_loaded",
            extra={
                "model_name": model_name,
                "model_variant": MODEL_VARIANT,
                "model_path": model_path,
            },
        )
    except Exception as e:
        logger.error(
            "model_load_failed",
            extra={
                "model_path": model_path,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        model_loaded.labels(model_version=model_name).set(0)
        raise


@app.on_event("startup")
async def startup_event():
    """Load model on startup."""
    load_model()


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Middleware to add correlation ID for request tracing."""
    # Get correlation ID from header or generate new one
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id

    response = await call_next(request)

    # Add correlation ID to response headers
    response.headers["X-Correlation-ID"] = correlation_id

    return response


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Middleware to track HTTP metrics and structured logging."""
    start_time = time.time()
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    # Log request start
    logger.info(
        "request_started",
        extra={
            "correlation_id": correlation_id,
            "method": request.method,
            "endpoint": request.url.path,
            "client_ip": get_remote_address(request),
        },
    )

    try:
        response = await call_next(request)
    except Exception as e:
        # Log error
        logger.error(
            "request_failed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "endpoint": request.url.path,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise

    # Record metrics
    duration = time.time() - start_time
    status_code = response.status_code
    method = request.method
    endpoint = request.url.path

    http_requests_total.labels(
        method=method, endpoint=endpoint, status=status_code
    ).inc()

    # Log request completion
    logger.info(
        "request_completed",
        extra={
            "correlation_id": correlation_id,
            "method": method,
            "endpoint": endpoint,
            "status_code": status_code,
            "duration_ms": duration * 1000,
        },
    )

    return response


@app.get("/health", response_model=HealthResponse)
async def health():
    """
    Health check endpoint.
    Returns service status and component health.
    """
    try:
        # Check model
        model_status = predictor is not None

        # Check Kafka (simplified - just check if we can import)
        kafka_status = True
        try:
            import kafka  # noqa: F401

            # Could add actual connection check here
        except ImportError:
            kafka_status = False

        status = "healthy" if (model_status and kafka_status) else "degraded"

        return HealthResponse(
            status=status,
            timestamp=datetime.utcnow().isoformat() + "Z",
            model_loaded=model_status,
            kafka_connected=kafka_status,
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")


@app.post("/predict", response_model=PredictResponse)
@limiter.limit(
    "1000/minute"
)  # Generous limit for demo (1000 requests per minute per IP)
async def predict(request: Request, predict_request: PredictRequest):
    """
    Make volatility predictions - Assignment API Contract.

    Accepts a list of feature rows and returns prediction scores.

    Example:
        POST /predict
        {"rows": [{"ret_mean": 0.05, "ret_std": 0.01, "n": 50}]}

        Response:
        {"scores": [0.74], "model_variant": "ml", "version": "v1.2", "ts": "2025-11-02T14:33:00Z"}
    """
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    if predictor is None:
        logger.error("model_not_loaded", extra={"correlation_id": correlation_id})
        raise HTTPException(status_code=503, detail="Model not loaded")

    try:
        logger.info(
            "prediction_requested",
            extra={
                "correlation_id": correlation_id,
                "rows_count": len(predict_request.rows),
                "model_version": MODEL_VERSION,
                "model_variant": MODEL_VARIANT,
            },
        )

        # Model expects these 10 features (from train.py prepare_features)
        expected_features = [
            "log_return_300s",
            "spread_mean_300s",
            "trade_intensity_300s",
            "order_book_imbalance_300s",
            "spread_mean_60s",
            "order_book_imbalance_60s",
            "price_velocity_300s",
            "realized_volatility_300s",
            "order_book_imbalance_30s",
            "realized_volatility_60s",
        ]

        # Map simple feature names to model features
        feature_mapping = {
            "ret_mean": "log_return_300s",
            "ret_std": "realized_volatility_300s",
            "n": "trade_intensity_300s",
        }

        # Process each row
        all_scores = []
        start_time = time.time()

        for row in predict_request.rows:
            # Convert row to dict
            row_dict = row.model_dump(exclude_none=True)
            features_dict = {}

            # Map simple names to full feature names
            for simple_name, full_name in feature_mapping.items():
                if simple_name in row_dict:
                    features_dict[full_name] = float(row_dict[simple_name])

            # Copy any full feature names that are already present
            for feat in expected_features:
                if feat in row_dict:
                    features_dict[feat] = float(row_dict[feat])

            # Ensure timestamp exists (required by prepare_features)
            features_dict["timestamp"] = datetime.utcnow().isoformat()
            features_dict["product_id"] = "BTC-USD"

            # Fill missing features with defaults
            for feat in expected_features:
                if feat not in features_dict:
                    features_dict[feat] = 0.0

            features_df = pd.DataFrame([features_dict])

            # Prepare features using same logic as training
            prepared_features = prepare_features_for_inference(features_df)

            # Make prediction
            result = predictor.predict(prepared_features)
            all_scores.append(round(result["probability"], 4))

        inference_time = time.time() - start_time

        # Determine model name for metrics
        model_name = "baseline" if MODEL_VARIANT == "baseline" else MODEL_VERSION

        # Record metrics
        prediction_latency_seconds.observe(inference_time)

        # Update last prediction timestamp for freshness tracking
        last_prediction_timestamp.set(time.time())

        for score in all_scores:
            prediction_label = "1" if score >= 0.5 else "0"
            predictions_total.labels(
                model_version=model_name, prediction=prediction_label
            ).inc()

        # Record real-time feature metrics (from last row processed)
        if features_dict:
            for feat_name in expected_features:
                if feat_name in features_dict:
                    try:
                        feature_value.labels(feature_name=feat_name).set(
                            float(features_dict[feat_name])
                        )
                    except (ValueError, TypeError):
                        pass

        # Record prediction score metrics
        if all_scores:
            latest_score = all_scores[-1]
            prediction_score.labels(model_variant=MODEL_VARIANT).set(latest_score)

            # Update rolling average
            global _recent_scores
            _recent_scores.extend(all_scores)
            if len(_recent_scores) > _MAX_RECENT_SCORES:
                _recent_scores = _recent_scores[-_MAX_RECENT_SCORES:]
            if _recent_scores:
                avg_score = sum(_recent_scores) / len(_recent_scores)
                avg_prediction_score.labels(model_variant=MODEL_VARIANT).set(avg_score)

        # Log successful prediction
        logger.info(
            "prediction_completed",
            extra={
                "correlation_id": correlation_id,
                "rows_count": len(all_scores),
                "scores": all_scores,
                "inference_time_ms": inference_time * 1000,
                "model_version": model_name,
                "model_variant": MODEL_VARIANT,
            },
        )

        return PredictResponse(
            scores=all_scores,
            model_variant=MODEL_VARIANT,
            version=f"v1.2-{model_name}",
            ts=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except Exception as e:
        logger.error(
            "prediction_failed",
            extra={
                "correlation_id": correlation_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")


@app.post("/predict/legacy", response_model=PredictionResponse)
@limiter.limit("1000/minute")
async def predict_legacy(request: Request, feature_request: FeatureRequest):
    """
    Legacy prediction endpoint (backward compatibility).

    Accepts feature dictionary and returns prediction, probability, and alert status.
    """
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    if predictor is None:
        logger.error("model_not_loaded", extra={"correlation_id": correlation_id})
        raise HTTPException(status_code=503, detail="Model not loaded")

    try:
        # Convert features to DataFrame (single row)
        features_dict = feature_request.features.copy()

        # Ensure timestamp exists (required by prepare_features)
        if "timestamp" not in features_dict:
            features_dict["timestamp"] = datetime.utcnow().isoformat()

        # Ensure product_id exists
        if "product_id" not in features_dict:
            features_dict["product_id"] = "BTC-USD"

        # Model expects these 10 features (from train.py prepare_features)
        expected_features = [
            "log_return_300s",
            "spread_mean_300s",
            "trade_intensity_300s",
            "order_book_imbalance_300s",
            "spread_mean_60s",
            "order_book_imbalance_60s",
            "price_velocity_300s",
            "realized_volatility_300s",
            "order_book_imbalance_30s",
            "realized_volatility_60s",
        ]

        # Fill missing features with defaults
        for feat in expected_features:
            if feat not in features_dict:
                features_dict[feat] = 0.0

        features_df = pd.DataFrame([features_dict])

        # Prepare features using same logic as training
        prepared_features = prepare_features_for_inference(features_df)

        # Make prediction
        start_time = time.time()
        result = predictor.predict(prepared_features)
        inference_time = time.time() - start_time

        # Determine model name for metrics
        model_name = "baseline" if MODEL_VARIANT == "baseline" else MODEL_VERSION

        # Record metrics
        prediction_latency_seconds.observe(inference_time)
        predictions_total.labels(
            model_version=model_name, prediction=str(result["prediction"])
        ).inc()

        return PredictionResponse(
            prediction=result["prediction"],
            probability=result["probability"],
            alert=result["alert"],
            inference_time_ms=result["inference_time_ms"],
            model_version=f"{model_name} ({MODEL_VARIANT})",
        )
    except Exception as e:
        logger.error(
            "prediction_failed",
            extra={
                "correlation_id": correlation_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")


@app.get("/version", response_model=VersionResponse)
async def version():
    """
    Get API and model version information.

    Returns model name, git SHA, version, and model variant.
    """
    # Get git SHA if available
    git_sha = os.getenv("GIT_SHA", "dev")
    if git_sha == "dev":
        # Try to read from git if available
        try:
            import subprocess

            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                git_sha = result.stdout.strip()
        except Exception:
            pass

    return VersionResponse(
        model=f"{MODEL_VERSION}_v1",
        sha=git_sha,
        version="v1.2",
        model_variant=MODEL_VARIANT,
        model_path=MODEL_PATH,
    )


@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint.
    Returns metrics in Prometheus format.
    """
    # Update system metrics before returning
    update_system_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Crypto Volatility Detection API",
        "version": "1.2.0",
        "model_variant": MODEL_VARIANT,
        "endpoints": {
            "/health": "Health check (GET)",
            "/predict": "Make predictions - Assignment API (POST)",
            "/predict/legacy": "Legacy prediction endpoint (POST)",
            "/version": "API version info (GET)",
            "/metrics": "Prometheus metrics (GET)",
            "/docs": "API documentation (Swagger)",
        },
        "example_request": {
            "url": "POST /predict",
            "body": {"rows": [{"ret_mean": 0.05, "ret_std": 0.01, "n": 50}]},
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
