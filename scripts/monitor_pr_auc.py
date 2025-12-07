#!/usr/bin/env python3
"""
Continuous PR-AUC monitoring for production model performance.
Runs every minute, calculates PR-AUC on recent predictions vs labels,
and logs metrics to MLflow for visualization.

This service:
1. Loads predictions from Kafka predictions.log topic or log files
2. Loads features with labels from features_live.parquet
3. Matches predictions with labels by timestamp
4. Calculates PR-AUC and related metrics
5. Logs to MLflow for time-series visualization
"""

import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
)

# Setup logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration from environment
MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC_PREDICTIONS = os.getenv("KAFKA_TOPIC_PREDICTIONS", "predictions.log")
PREDICTIONS_LOG_DIR = Path(os.getenv("PREDICTIONS_LOG_DIR", "logs/predictions"))
FEATURES_LIVE_PATH = Path(
    os.getenv("FEATURES_LIVE_PATH", "data/processed/features_live.parquet")
)
UPDATE_INTERVAL_SECONDS = int(
    os.getenv("PR_AUC_UPDATE_INTERVAL", "60")
)  # 1 minute default
TIME_WINDOW_HOURS = float(
    os.getenv("PR_AUC_TIME_WINDOW", "1")
)  # Last N hours default (supports fractional hours like 0.5)
LOOKBACK_BUFFER_MINUTES = int(
    os.getenv("PR_AUC_LOOKBACK_BUFFER", "5")
)  # Only use predictions from at least 5 minutes ago (to ensure features exist)
KAFKA_CONSUMER_TIMEOUT_MS = int(
    os.getenv("KAFKA_CONSUMER_TIMEOUT_MS", "120000")
)  # 2 minutes default (increased from 5 seconds)
KAFKA_MAX_MESSAGES = int(
    os.getenv("KAFKA_MAX_MESSAGES", "50000")
)  # Max messages to read per cycle
EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT", "crypto-volatility-production")

# Global flag for graceful shutdown
running = True


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global running
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_predictions_from_kafka(
    time_window_hours: int = TIME_WINDOW_HOURS,
    lookback_buffer_minutes: int = LOOKBACK_BUFFER_MINUTES,
) -> Optional[pd.DataFrame]:
    """Load predictions from Kafka topic with improved timeout and filtering."""
    try:
        from kafka import KafkaConsumer

        logger.info(
            f"Loading predictions from Kafka topic {KAFKA_TOPIC_PREDICTIONS} "
            f"(timeout: {KAFKA_CONSUMER_TIMEOUT_MS/1000:.1f}s, window: {time_window_hours}h)"
        )
        # Use lookback buffer: only get predictions from at least X minutes ago
        # This ensures features have been written and labels have been calculated
        # Always use timezone-aware timestamps (UTC) to avoid naive/aware arithmetic errors
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(hours=time_window_hours)
        max_time = now - timedelta(
            minutes=lookback_buffer_minutes
        )  # Don't use very recent predictions

        logger.debug(
            f"Time window: {cutoff_time} to {max_time} "
            f"(excludes last {lookback_buffer_minutes} minutes)"
        )

        # Use a fixed consumer group to maintain offset position
        # Read from latest (end of topic) and seek backwards to get recent messages
        # This is more efficient than reading from earliest and filtering through old messages
        consumer_group = "pr-auc-monitor"
        consumer = KafkaConsumer(
            KAFKA_TOPIC_PREDICTIONS,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="latest",  # Start from end if no committed offset
            consumer_timeout_ms=KAFKA_CONSUMER_TIMEOUT_MS,  # Configurable timeout
            enable_auto_commit=True,  # Commit offsets to remember position
            group_id=consumer_group,  # Fixed group to maintain offset
        )

        # Seek to end first, then seek backwards to read recent messages
        # This allows us to read the last N messages efficiently
        try:
            # Wait for partition assignment (consumer joins group on poll)
            poll_deadline = time.time() + 8.0
            while not consumer.assignment() and time.time() < poll_deadline:
                consumer.poll(timeout_ms=300)

            partitions = list(consumer.assignment())
            if not partitions:
                logger.warning(
                    "No partitions assigned after waiting; will read from committed offset"
                )
            else:
                consumer.seek_to_end()
                end_offsets = consumer.end_offsets(partitions)

                messages_to_read = int(
                    time_window_hours * 3600 * 2
                )  # 2x buffer for safety

                for partition in partitions:
                    end_offset = end_offsets.get(partition, 0)
                    if end_offset > 0:
                        seek_offset = max(0, end_offset - messages_to_read)
                        consumer.seek(partition, seek_offset)
                        logger.debug(
                            f"Seeked partition {partition} to offset {seek_offset} "
                            f"(end: {end_offset}, reading last ~{messages_to_read} messages)"
                        )
        except Exception as e:
            logger.warning(
                f"Could not seek in topic: {e}, will read from committed offset"
            )

        predictions = []
        message_count = 0
        skipped_recent = 0
        skipped_old = 0
        skipped_invalid = 0
        processed_count = 0
        consecutive_old_messages = 0  # Track consecutive old messages
        max_consecutive_old = (
            100  # If we see 100 consecutive old messages, we've passed the window
        )
        error_count = 0  # Surface the first few parsing errors at warning level

        # Read messages and filter by time window
        start_time = time.time()
        max_processing_time = min(
            KAFKA_CONSUMER_TIMEOUT_MS / 1000, 20
        )  # Max 20 seconds
        last_message_time = None

        try:
            for message in consumer:
                # Hard time limit - break after max_processing_time seconds
                elapsed = time.time() - start_time
                if elapsed > max_processing_time:
                    logger.info(
                        f"Reached max processing time ({max_processing_time:.1f}s), stopping read. Processed {processed_count} messages."
                    )
                    break

                processed_count += 1
                try:
                    pred = message.value
                    if pred is None:
                        continue

                    # Enforce UTC to avoid offset-aware vs naive comparisons
                    pred_time = pd.to_datetime(
                        pred.get("timestamp"), utc=True, errors="coerce"
                    )
                    if pd.isna(pred_time):
                        skipped_invalid += 1
                        if skipped_invalid <= 3:
                            logger.warning(
                                f"Skipping prediction with invalid timestamp: {pred.get('timestamp')}"
                            )
                        continue
                    last_message_time = pred_time

                    # Calculate age of message
                    age_minutes = (now - pred_time).total_seconds() / 60

                    # Filter by time window
                    if pred_time < cutoff_time:
                        skipped_old += 1
                        consecutive_old_messages += 1
                        # Log first few old messages for debugging
                        if skipped_old <= 3:
                            logger.debug(
                                f"Skipping old message: age={age_minutes:.1f}min, "
                                f"timestamp={pred_time}, cutoff={cutoff_time}"
                            )
                        # If we've seen many consecutive old messages and we have some in window, we're done
                        if (
                            consecutive_old_messages > max_consecutive_old
                            and message_count > 0
                        ):
                            logger.info(
                                f"Processed {consecutive_old_messages} consecutive old messages, stopping read"
                            )
                            break
                        # Also break if we've processed too many old messages total
                        if skipped_old > 2000 and message_count > 0:
                            logger.info(
                                f"Processed {skipped_old} old messages, stopping to avoid timeout"
                            )
                            break
                        continue
                    elif pred_time > max_time:
                        skipped_recent += 1
                        consecutive_old_messages = 0  # Reset counter
                        # Log first few recent messages for debugging
                        if skipped_recent <= 3:
                            logger.debug(
                                f"Skipping recent message: age={age_minutes:.1f}min, "
                                f"timestamp={pred_time}, max_time={max_time}"
                            )
                        # If we're getting recent messages but already have some in window, we might be done
                        # But continue a bit more to catch any messages in the window
                        continue
                    else:
                        # Within time window
                        consecutive_old_messages = 0  # Reset counter
                        predictions.append(pred)
                        message_count += 1
                        # Log first few matched messages for debugging
                        if message_count <= 3:
                            logger.debug(
                                f"Matched message in window: age={age_minutes:.1f}min, "
                                f"timestamp={pred_time}"
                            )

                    # Safety limit to prevent memory issues
                    if message_count >= KAFKA_MAX_MESSAGES:
                        logger.warning(
                            f"Reached message limit ({KAFKA_MAX_MESSAGES}), stopping read. "
                            f"Processed {processed_count} messages total."
                        )
                        break

                except Exception as e:
                    error_count += 1
                    log_fn = logger.warning if error_count <= 3 else logger.debug
                    log_fn(f"Error processing message {processed_count}: {e}")
                    continue
        except StopIteration:
            # This is raised when consumer_timeout_ms expires (no messages received)
            logger.info("Kafka consumer timeout reached (no new messages)")
        except Exception as e:
            logger.warning(f"Error reading from Kafka consumer: {e}")

        elapsed_time = time.time() - start_time
        consumer.close()

        logger.info(
            f"Kafka read complete: {elapsed_time:.2f}s, "
            f"processed {processed_count} messages, "
            f"matched {message_count} in window, "
            f"skipped {skipped_old} old, {skipped_recent} recent, "
            f"skipped {skipped_invalid} invalid"
        )

        # Enhanced diagnostics when no matches found
        if not predictions:
            logger.warning(
                f"No predictions found in Kafka topic "
                f"(last {time_window_hours}h, excluding last {lookback_buffer_minutes}min). "
                f"Processed {processed_count} messages, skipped {skipped_old} old, {skipped_recent} recent."
            )

            # Log time window details for debugging
            logger.info(
                f"Time window details: cutoff={cutoff_time} ({time_window_hours}h ago), "
                f"max_time={max_time} ({lookback_buffer_minutes}min ago), "
                f"current_time={now}"
            )

            # Log sample timestamps if available
            if last_message_time is not None:
                last_age_minutes = (now - last_message_time).total_seconds() / 60
                logger.info(
                    f"Last message timestamp: {last_message_time}, "
                    f"age: {last_age_minutes:.1f} minutes"
                )

            # Provide diagnosis hints
            if skipped_recent > skipped_old:
                logger.warning(
                    f"Most messages are too recent (skipped {skipped_recent} recent vs {skipped_old} old). "
                    f"This suggests predictions are being made faster than the {lookback_buffer_minutes}min buffer. "
                    f"Consider increasing LOOKBACK_BUFFER_MINUTES or waiting longer."
                )
            elif skipped_old > skipped_recent:
                logger.warning(
                    f"Most messages are too old (skipped {skipped_old} old vs {skipped_recent} recent). "
                    f"This suggests the time window ({time_window_hours}h) may be too narrow, "
                    f"or predictions stopped being generated. Consider increasing PR_AUC_TIME_WINDOW."
                )
            else:
                logger.warning(
                    "Equal mix of old and recent messages suggests timestamp parsing issues "
                    "or messages outside the expected time range."
                )
            if skipped_invalid > 0:
                logger.warning(
                    f"Skipped {skipped_invalid} messages with invalid timestamps; ensure prediction logs include a valid ISO8601 timestamp in UTC."
                )

            return None

        df = pd.DataFrame(predictions)
        logger.info(
            f"✓ Loaded {len(df)} predictions from Kafka "
            f"(time window: {cutoff_time} to {max_time})"
        )
        return df

    except Exception as e:
        logger.error(f"Could not load predictions from Kafka: {e}")
        import traceback

        logger.debug(traceback.format_exc())
        return None


def load_predictions_from_files(
    time_window_hours: int = TIME_WINDOW_HOURS,
    lookback_buffer_minutes: int = LOOKBACK_BUFFER_MINUTES,
) -> Optional[pd.DataFrame]:
    """Load predictions from NDJSON log files."""
    if not PREDICTIONS_LOG_DIR.exists():
        logger.warning(f"Predictions directory not found: {PREDICTIONS_LOG_DIR}")
        return None

    logger.info(f"Loading predictions from {PREDICTIONS_LOG_DIR}...")
    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(hours=time_window_hours)
    max_time = now - timedelta(
        minutes=lookback_buffer_minutes
    )  # Don't use very recent predictions
    predictions = []

    # Find all NDJSON files, sorted by modification time (newest first)
    log_files = sorted(
        PREDICTIONS_LOG_DIR.glob("predictions_*.ndjson"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for log_file in log_files:
        try:
            with open(log_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        pred = json.loads(line)
                        pred_time = pd.to_datetime(pred.get("timestamp"), utc=True)
                        # Only include predictions within time window and not too recent
                        if cutoff_time <= pred_time <= max_time:
                            predictions.append(pred)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception as e:
            logger.warning(f"Error reading {log_file}: {e}")
            continue

        # Stop if we have enough data
        if len(predictions) > 10000:
            break

    if not predictions:
        logger.warning(
            f"No predictions found in log files "
            f"(last {time_window_hours}h, excluding last {lookback_buffer_minutes}min)"
        )
        return None

    df = pd.DataFrame(predictions)
    logger.info(
        f"Loaded {len(df)} predictions from log files "
        f"(last {time_window_hours}h, excluding last {lookback_buffer_minutes}min for feature availability)"
    )
    return df


def load_features_with_labels() -> Optional[pd.DataFrame]:
    """Load features with volatility_spike labels."""
    if not FEATURES_LIVE_PATH.exists():
        logger.warning(f"Features file not found: {FEATURES_LIVE_PATH}")
        return None

    logger.info(f"Loading features with labels from {FEATURES_LIVE_PATH}...")
    try:
        df = pd.read_parquet(FEATURES_LIVE_PATH)

        if "volatility_spike" not in df.columns:
            logger.warning("Features file does not have volatility_spike labels")
            return None

        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)

        # Filter to only rows with labels (some may be NaN due to forward-looking requirement)
        df_labeled = df[df["volatility_spike"].notna()].copy()

        logger.info(
            f"Loaded {len(df_labeled)} features with labels "
            f"(out of {len(df)} total rows)"
        )

        return df_labeled

    except Exception as e:
        logger.error(f"Error loading features: {e}")
        return None


def match_predictions_with_labels(
    predictions_df: pd.DataFrame,
    features_df: pd.DataFrame,
    time_tolerance_seconds: int = 30,  # Increased from 5 to 30 seconds for better matching
) -> Optional[pd.DataFrame]:
    """Match predictions with labels by timestamp."""
    if (
        "timestamp" not in predictions_df.columns
        or "timestamp" not in features_df.columns
    ):
        logger.error("Missing timestamp columns for matching")
        return None

    predictions_df = predictions_df.copy()
    features_df = features_df.copy()

    # Convert timestamps with explicit UTC to avoid naive/aware mismatches
    predictions_df["timestamp"] = pd.to_datetime(predictions_df["timestamp"], utc=True)
    features_df["timestamp"] = pd.to_datetime(features_df["timestamp"], utc=True)

    # Log time ranges for debugging
    logger.info(
        f"Prediction time range: {predictions_df['timestamp'].min()} to {predictions_df['timestamp'].max()}"
    )
    logger.info(
        f"Feature time range: {features_df['timestamp'].min()} to {features_df['timestamp'].max()}"
    )

    # Sort by timestamp
    predictions_df = predictions_df.sort_values("timestamp").reset_index(drop=True)
    features_df = features_df.sort_values("timestamp").reset_index(drop=True)

    # Merge using merge_asof (nearest match within tolerance)
    merged = pd.merge_asof(
        predictions_df,
        features_df[["timestamp", "volatility_spike"]],
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(seconds=time_tolerance_seconds),
    )

    # Filter to only rows with valid labels
    merged = merged[merged["volatility_spike"].notna()].copy()

    if len(merged) > 0:
        spikes = merged["volatility_spike"].sum()
        normal = (merged["volatility_spike"] == 0).sum()
        logger.info(
            f"Matched {len(merged)} predictions with labels "
            f"(out of {len(predictions_df)} predictions, tolerance: {time_tolerance_seconds}s)"
        )
        logger.info(
            f"Matched data: {spikes} spikes, {normal} normal "
            f"({spikes/len(merged)*100:.1f}% spikes)"
        )
    else:
        logger.warning(
            f"Matched 0 predictions with labels "
            f"(out of {len(predictions_df)} predictions, tolerance: {time_tolerance_seconds}s)"
        )
        # Try to diagnose why matching failed
        if len(predictions_df) > 0 and len(features_df) > 0:
            pred_min = predictions_df["timestamp"].min()
            pred_max = predictions_df["timestamp"].max()
            feat_min = features_df["timestamp"].min()
            feat_max = features_df["timestamp"].max()

            if pred_max < feat_min:
                logger.warning(
                    f"Predictions are all before features: pred_max ({pred_max}) < feat_min ({feat_min})"
                )
            elif pred_min > feat_max:
                logger.warning(
                    f"Predictions are all after features: pred_min ({pred_min}) > feat_max ({feat_max})"
                )
            else:
                # There's overlap, but matching still failed - timestamps might be too far apart
                logger.warning(
                    f"Time ranges overlap but no matches found. "
                    f"Consider increasing time_tolerance_seconds (current: {time_tolerance_seconds})"
                )

    return merged


def calculate_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, y_proba: np.ndarray
) -> dict:
    """Calculate classification metrics."""
    try:
        pr_auc = average_precision_score(y_true, y_proba)
    except ValueError:
        pr_auc = 0.0

    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)

    cm = confusion_matrix(y_true, y_pred)
    if cm.size == 4:
        tn, fp, fn, tp = cm.ravel()
    else:
        tn = fp = fn = tp = 0

    return {
        "pr_auc": float(pr_auc),
        "precision": float(precision),
        "recall": float(recall),
        "f1_score": float(f1),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "true_negatives": int(tn),
        "false_negatives": int(fn),
        "total_samples": len(y_true),
        "positive_samples": int(y_true.sum()),
        "negative_samples": int((y_true == 0).sum()),
    }


def log_metrics_to_mlflow(metrics: dict, model_version: str = "production"):
    """Log metrics to MLflow for time-series visualization."""
    try:
        mlflow.set_tracking_uri(MLFLOW_URI)
        mlflow.set_experiment(EXPERIMENT_NAME)

        # Create or get the continuous run for production monitoring
        # Use a fixed run name so metrics accumulate in one run
        run_name = f"production_monitoring_{model_version}"

        # Try to get existing run, or create new one
        try:
            from mlflow.tracking import MlflowClient

            client = MlflowClient()
            experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
            if experiment is None:
                mlflow.create_experiment(EXPERIMENT_NAME)
                experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)

            runs = client.search_runs(
                experiment_ids=[experiment.experiment_id],
                filter_string=f"tags.mlflow.runName = '{run_name}'",
                max_results=1,
                order_by=["start_time DESC"],  # Get most recent run first
            )
            if runs:
                existing_run = runs[0]
                # Only reuse if the run is still active (not finished)
                if existing_run.info.status == "RUNNING":
                    run_id = existing_run.info.run_id
                    logger.info(f"Reusing active MLflow run: {run_id}")
                else:
                    # Run is finished, create a new one
                    run_id = None
                    logger.info(
                        f"Existing run {existing_run.info.run_id} is {existing_run.info.status}, "
                        f"creating new run"
                    )
            else:
                run_id = None
                logger.info(f"No existing run found, creating new run: {run_name}")
        except Exception as e:
            logger.debug(f"Could not find existing run: {e}")
            run_id = None

        with mlflow.start_run(run_name=run_name, run_id=run_id):
            # Log metrics with current timestamp (MLflow will create time-series chart)
            current_time = datetime.now()

            mlflow.log_metrics(
                {
                    "pr_auc": metrics["pr_auc"],
                    "precision": metrics["precision"],
                    "recall": metrics["recall"],
                    "f1_score": metrics["f1_score"],
                    "true_positives": metrics["true_positives"],
                    "false_positives": metrics["false_positives"],
                    "true_negatives": metrics["true_negatives"],
                    "false_negatives": metrics["false_negatives"],
                    "total_samples": metrics["total_samples"],
                    "positive_samples": metrics["positive_samples"],
                    "negative_samples": metrics["negative_samples"],
                },
                step=int(
                    current_time.timestamp()
                ),  # Use timestamp as step for time-series
            )

            # Log parameters (model version, time window, etc.)
            mlflow.log_params(
                {
                    "model_version": model_version,
                    "time_window_hours": TIME_WINDOW_HOURS,
                    "update_interval_seconds": UPDATE_INTERVAL_SECONDS,
                }
            )

            logger.info(
                f"✓ Logged metrics to MLflow: PR-AUC={metrics['pr_auc']:.4f}, "
                f"Precision={metrics['precision']:.4f}, Recall={metrics['recall']:.4f}, "
                f"F1={metrics['f1_score']:.4f}, Samples={metrics['total_samples']}"
            )

    except Exception as e:
        logger.error(f"Failed to log metrics to MLflow: {e}")
        import traceback

        traceback.print_exc()


def calculate_and_log_pr_auc():
    """Main function to calculate PR-AUC and log to MLflow."""
    logger.info("=" * 70)
    logger.info("Calculating PR-AUC for production monitoring")
    logger.info("=" * 70)

    # Load predictions (try Kafka first, then files)
    # Use lookback buffer to ensure features exist and have labels
    predictions_df = load_predictions_from_kafka(
        time_window_hours=TIME_WINDOW_HOURS,
        lookback_buffer_minutes=LOOKBACK_BUFFER_MINUTES,
    )
    if predictions_df is None:
        predictions_df = load_predictions_from_files(
            time_window_hours=TIME_WINDOW_HOURS,
            lookback_buffer_minutes=LOOKBACK_BUFFER_MINUTES,
        )

    if predictions_df is None or len(predictions_df) == 0:
        logger.warning("No predictions available, skipping PR-AUC calculation")
        return

    # Load features with labels
    features_df = load_features_with_labels()
    if features_df is None or len(features_df) == 0:
        logger.warning("No features with labels available, skipping PR-AUC calculation")
        return

    # Match predictions with labels
    matched_df = match_predictions_with_labels(predictions_df, features_df)
    if matched_df is None or len(matched_df) == 0:
        logger.warning(
            "No matched predictions with labels, skipping PR-AUC calculation"
        )
        return

    # Extract arrays for metrics calculation
    y_true = matched_df["volatility_spike"].values.astype(int)
    y_pred = matched_df["prediction"].values.astype(int)
    y_proba = matched_df["score"].values.astype(float)

    # Calculate metrics
    metrics = calculate_metrics(y_true, y_pred, y_proba)

    # Get model version from predictions (use most common)
    model_version = (
        matched_df["model_version"].mode()[0]
        if "model_version" in matched_df.columns
        else "production"
    )

    # Log to MLflow
    log_metrics_to_mlflow(metrics, model_version)

    logger.info("=" * 70)


def main():
    """Main loop - runs continuously, updating every UPDATE_INTERVAL_SECONDS."""
    logger.info("=" * 70)
    logger.info("Starting PR-AUC Monitoring Service")
    logger.info("=" * 70)
    logger.info(f"MLflow URI: {MLFLOW_URI}")
    logger.info(
        f"Update interval: {UPDATE_INTERVAL_SECONDS} seconds ({UPDATE_INTERVAL_SECONDS/60:.1f} minutes)"
    )
    logger.info(f"Time window: {TIME_WINDOW_HOURS} hours")
    logger.info(
        f"Lookback buffer: {LOOKBACK_BUFFER_MINUTES} minutes (excludes recent predictions)"
    )
    logger.info(f"Kafka consumer timeout: {KAFKA_CONSUMER_TIMEOUT_MS/1000:.1f} seconds")
    logger.info(f"Max messages per cycle: {KAFKA_MAX_MESSAGES:,}")
    logger.info(f"Features path: {FEATURES_LIVE_PATH}")
    logger.info(
        f"Predictions: Kafka topic {KAFKA_TOPIC_PREDICTIONS} or {PREDICTIONS_LOG_DIR}"
    )
    logger.info("=" * 70)

    # Initial calculation
    try:
        calculate_and_log_pr_auc()
    except Exception as e:
        logger.error(f"Error in initial PR-AUC calculation: {e}")
        import traceback

        traceback.print_exc()

    # Continuous loop
    while running:
        try:
            time.sleep(UPDATE_INTERVAL_SECONDS)
            if running:  # Check again after sleep
                calculate_and_log_pr_auc()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            break
        except Exception as e:
            logger.error(f"Error in PR-AUC calculation loop: {e}")
            import traceback

            traceback.print_exc()
            # Continue running even if there's an error

    logger.info("PR-AUC Monitoring Service stopped")


if __name__ == "__main__":
    main()
