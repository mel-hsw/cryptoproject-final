"""
Generate Evidently report to monitor model performance on live production data.

Supports production monitoring report types:
1. production_monitoring: Compares reference (training) data vs current production data
   - Data drift: Feature distribution changes
   - Prediction drift: Model prediction distribution changes
   - Data quality: Missing values, data quality issues
   
2. data_drift: Compares early vs late windows of collected production data
3. train_test: Compares train vs test datasets (for historical analysis)

For production monitoring, you need:
- Reference data: Training/historical features (with labels if available)
- Current data: Live production features from featurizer (features_live.parquet)
- Predictions: Optional - prediction logs from Kafka (predictions.log) or files
"""

import argparse
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple

from evidently.report import Report
from evidently.metrics import (
    DatasetDriftMetric,
    ColumnDriftMetric,
    DatasetMissingValuesMetric,
    DatasetSummaryMetric,
    RegressionQualityMetric,
    ClassificationQualityMetric,
)

import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# PredictionDriftMetric may not be available in all Evidently versions
try:
    from evidently.metrics import PredictionDriftMetric
    HAS_PREDICTION_DRIFT = True
except ImportError:
    HAS_PREDICTION_DRIFT = False
    logger.warning("PredictionDriftMetric not available in this Evidently version")


def load_and_split_data(features_path: str, split_ratio: float = 0.5):
    """
    Load features and split into reference (early) and current (late) datasets.

    Args:
        features_path: Path to features parquet file
        split_ratio: Fraction of data to use as reference (default 0.5)

    Returns:
        reference_df, current_df
    """
    logger.info(f"Loading features from {features_path}")
    df = pd.read_parquet(features_path)

    # Convert timestamp if needed
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

    # Split into reference (early) and current (late)
    split_idx = int(len(df) * split_ratio)

    reference = df.iloc[:split_idx].copy()
    current = df.iloc[split_idx:].copy()

    logger.info("Split data:")
    logger.info(f"  Reference: {len(reference)} rows")
    logger.info(f"  Current: {len(current)} rows")

    if "timestamp" in df.columns:
        logger.info(
            f"  Reference time: {reference['timestamp'].min()} to {reference['timestamp'].max()}"
        )
        logger.info(
            f"  Current time: {current['timestamp'].min()} to {current['timestamp'].max()}"
        )

    return reference, current


def load_train_test_split(
    features_path: str, val_size: float = 0.15, test_size: float = 0.15
):
    """
    Load features and split into train/val/test with time-based split.
    Same logic as feature_preparation.py for consistency.

    Args:
        features_path: Path to features parquet file
        val_size: Fraction for validation set (default 0.15)
        test_size: Fraction for test set (default 0.15)

    Returns:
        train_df, test_df (for drift comparison)
    """
    logger.info(f"Loading features from {features_path}")
    df = pd.read_parquet(features_path)
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Time-based split (same as training script)
    n = len(df)
    train_end = int(n * (1 - val_size - test_size))
    val_end = int(n * (1 - test_size))

    train_df = df.iloc[:train_end].copy()
    val_df = df.iloc[train_end:val_end].copy()
    test_df = df.iloc[val_end:].copy()

    logger.info("Train/test split:")
    logger.info(f"  Train: {len(train_df)} rows ({len(train_df)/n*100:.1f}%)")
    logger.info(f"  Val:   {len(val_df)} rows ({len(val_df)/n*100:.1f}%)")
    logger.info(f"  Test:  {len(test_df)} rows ({len(test_df)/n*100:.1f}%)")

    if "timestamp" in df.columns:
        logger.info(
            f"  Train time: {train_df['timestamp'].min()} to {train_df['timestamp'].max()}"
        )
        logger.info(
            f"  Test time:  {test_df['timestamp'].min()} to {test_df['timestamp'].max()}"
        )

    return train_df, test_df


def select_features_for_drift(df: pd.DataFrame) -> list:
    """
    Select features for drift analysis using MODEL_FEATURES from feature_preparation.py.
    This ensures we analyze the same features that are actually used in the model.

    Uses MODEL_FEATURES (10 features) - the same features computed by featurizer
    and used by the inference model.

    Args:
        df: DataFrame with features

    Returns:
        List of column names to analyze
    """
    # Import MODEL_FEATURES from feature_preparation.py (single source of truth)
    import sys
    from pathlib import Path
    
    models_dir = Path(__file__).parent.parent / "models"
    if str(models_dir) not in sys.path:
        sys.path.insert(0, str(models_dir))
    
    from feature_preparation import MODEL_FEATURES  # noqa: E402
    
    priority_features = MODEL_FEATURES

    # Select available features
    available_cols = [col for col in priority_features if col in df.columns]

    # Create derived feature: return range (return_max - return_min)
    # Note: This is computed on-the-fly for both reference and current datasets in generate_report
    if "return_max_60s" in df.columns and "return_min_60s" in df.columns:
        if "return_range_60s" not in available_cols:
            available_cols.append("return_range_60s")

    if not available_cols:
        logger.warning(
            "No matching feature columns found. Falling back to all numeric columns."
        )
        # Fallback: get numeric columns excluding metadata
        numeric_cols = df.select_dtypes(include=["float64", "int64"]).columns.tolist()
        exclude = ["timestamp", "label", "future_volatility", "volatility_spike"]
        available_cols = [col for col in numeric_cols if col not in exclude]

    logger.info(
        f"Selected {len(available_cols)} features for drift analysis: {available_cols}"
    )
    return available_cols


def load_live_features(features_path: str, time_window_hours: Optional[int] = None) -> pd.DataFrame:
    """
    Load live production features from featurizer output.
    
    Args:
        features_path: Path to features_live.parquet file
        time_window_hours: Optional - only load data from last N hours (default: all data)
        
    Returns:
        DataFrame with live production features
    """
    logger.info(f"Loading live production features from {features_path}")
    df = pd.read_parquet(features_path)
    
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        
        if time_window_hours:
            cutoff_time = datetime.now() - timedelta(hours=time_window_hours)
            df = df[df["timestamp"] >= cutoff_time].copy()
            logger.info(f"Filtered to last {time_window_hours} hours: {len(df)} rows")
    
    logger.info(f"Loaded {len(df)} rows of live production data")
    if "timestamp" in df.columns:
        logger.info(f"  Time range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    
    return df


def load_predictions_from_kafka_topic(
    kafka_bootstrap: str = "localhost:9092",
    topic: str = "predictions.log",
    time_window_hours: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """
    Load predictions from Kafka topic.
    
    Args:
        kafka_bootstrap: Kafka bootstrap servers
        topic: Kafka topic name
        time_window_hours: Optional - only load predictions from last N hours
        
    Returns:
        DataFrame with predictions or None if Kafka unavailable
    """
    try:
        from kafka import KafkaConsumer
        from kafka.errors import KafkaError
        
        logger.info(f"Loading predictions from Kafka topic {topic}...")
        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=kafka_bootstrap,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="earliest",
            consumer_timeout_ms=5000,  # 5 second timeout
        )
        
        predictions = []
        for message in consumer:
            pred = message.value
            if time_window_hours:
                pred_time = pd.to_datetime(pred.get("timestamp"))
                cutoff_time = datetime.now() - timedelta(hours=time_window_hours)
                if pred_time < cutoff_time:
                    continue
            predictions.append(pred)
        
        consumer.close()
        
        if not predictions:
            logger.warning("No predictions found in Kafka topic")
            return None
        
        df = pd.DataFrame(predictions)
        logger.info(f"Loaded {len(df)} predictions from Kafka")
        return df
        
    except Exception as e:
        logger.warning(f"Could not load predictions from Kafka: {e}")
        return None


def load_predictions_from_files(
    predictions_dir: str = "logs/predictions",
    time_window_hours: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """
    Load predictions from NDJSON log files.
    
    Args:
        predictions_dir: Directory containing prediction log files
        time_window_hours: Optional - only load predictions from last N hours
        
    Returns:
        DataFrame with predictions or None if no files found
    """
    predictions_path = Path(predictions_dir)
    if not predictions_path.exists():
        logger.warning(f"Predictions directory not found: {predictions_dir}")
        return None
    
    logger.info(f"Loading predictions from {predictions_dir}...")
    predictions = []
    
    # Find all NDJSON files
    for log_file in predictions_path.glob("predictions_*.ndjson"):
        try:
            with open(log_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        pred = json.loads(line)
                        if time_window_hours:
                            pred_time = pd.to_datetime(pred.get("timestamp"))
                            cutoff_time = datetime.now() - timedelta(hours=time_window_hours)
                            if pred_time < cutoff_time:
                                continue
                        predictions.append(pred)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"Error reading {log_file}: {e}")
            continue
    
    if not predictions:
        logger.warning("No predictions found in log files")
        return None
    
    df = pd.DataFrame(predictions)
    logger.info(f"Loaded {len(df)} predictions from log files")
    return df


def merge_features_with_predictions(
    features_df: pd.DataFrame,
    predictions_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge features with predictions on timestamp.
    
    Args:
        features_df: DataFrame with features (must have timestamp)
        predictions_df: DataFrame with predictions (must have timestamp)
        
    Returns:
        Merged DataFrame with features and predictions
    """
    if "timestamp" not in features_df.columns or "timestamp" not in predictions_df.columns:
        logger.warning("Cannot merge: missing timestamp columns")
        return features_df
    
    features_df = features_df.copy()
    predictions_df = predictions_df.copy()
    
    # Convert timestamps
    features_df["timestamp"] = pd.to_datetime(features_df["timestamp"])
    predictions_df["timestamp"] = pd.to_datetime(predictions_df["timestamp"])
    
    # Merge on timestamp (within 1 second tolerance)
    merged = pd.merge_asof(
        features_df.sort_values("timestamp"),
        predictions_df[["timestamp", "score", "prediction"]].sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(seconds=1),
    )
    
    logger.info(f"Merged {len(merged)} rows (features: {len(features_df)}, predictions: {len(predictions_df)})")
    return merged


def generate_report(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    output_path: str = "reports/evidently/data_drift_report.html",
    reference_predictions: Optional[pd.DataFrame] = None,
    current_predictions: Optional[pd.DataFrame] = None,
    include_prediction_drift: bool = True,
):
    """
    Generate Evidently report comparing reference and current data.
    Only analyzes the same feature columns used in training/inference.

    Args:
        reference_df: Reference (training/historical) dataset
        current_df: Current (production) dataset
        output_path: Where to save the HTML report
        reference_predictions: Optional - reference predictions for prediction drift
        current_predictions: Optional - current predictions for prediction drift
        include_prediction_drift: Whether to include prediction drift metrics
    """
    # Create output directory
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    logger.info("Generating Evidently report...")

    # Select only the features used in training/inference
    feature_cols = select_features_for_drift(reference_df)

    # Compute derived features if needed (e.g., return_range_60s)
    reference_df = reference_df.copy()
    current_df = current_df.copy()
    if "return_range_60s" in feature_cols:
        if (
            "return_max_60s" in reference_df.columns
            and "return_min_60s" in reference_df.columns
        ):
            reference_df["return_range_60s"] = (
                reference_df["return_max_60s"] - reference_df["return_min_60s"]
            )
        if (
            "return_max_60s" in current_df.columns
            and "return_min_60s" in current_df.columns
        ):
            current_df["return_range_60s"] = (
                current_df["return_max_60s"] - current_df["return_min_60s"]
            )

    # Ensure both datasets have the same columns
    common_cols = [
        col
        for col in feature_cols
        if col in reference_df.columns and col in current_df.columns
    ]
    if len(common_cols) != len(feature_cols):
        missing = set(feature_cols) - set(common_cols)
        logger.warning(f"Some features missing in datasets: {missing}")

    # Filter to only include relevant feature columns (plus timestamp for context)
    cols_to_include = common_cols.copy()
    if "timestamp" in reference_df.columns:
        cols_to_include.insert(0, "timestamp")

    reference_filtered = reference_df[cols_to_include].copy()
    current_filtered = current_df[cols_to_include].copy()

    logger.info(f"Analyzing {len(common_cols)} feature columns: {common_cols}")

    # Create report with data quality and drift analysis
    # ColumnDriftMetric requires one metric per column, so create metrics for each feature
    drift_metrics = [ColumnDriftMetric(column_name=col) for col in common_cols]
    
    # Base metrics for all reports
    metrics = [
        DatasetSummaryMetric(),
        DatasetMissingValuesMetric(),
        DatasetDriftMetric(),
        *drift_metrics,  # Unpack list of ColumnDriftMetric metrics
    ]
    
    # Add prediction drift if predictions are available
    if include_prediction_drift and reference_predictions is not None and current_predictions is not None:
        if HAS_PREDICTION_DRIFT:
            logger.info("Adding prediction drift metrics...")
            metrics.append(PredictionDriftMetric())
        else:
            logger.warning("PredictionDriftMetric not available, skipping prediction drift")
    
    # Prepare data for prediction drift (if available)
    reference_for_report = reference_filtered.copy()
    current_for_report = current_filtered.copy()
    
    if include_prediction_drift and reference_predictions is not None and current_predictions is not None:
        # Merge predictions with features
        if "timestamp" in reference_df.columns and "timestamp" in reference_predictions.columns:
            reference_merged = merge_features_with_predictions(reference_df, reference_predictions)
            if "prediction" in reference_merged.columns:
                reference_for_report["prediction"] = reference_merged["prediction"].values[:len(reference_for_report)]
        
        if "timestamp" in current_df.columns and "timestamp" in current_predictions.columns:
            current_merged = merge_features_with_predictions(current_df, current_predictions)
            if "prediction" in current_merged.columns:
                current_for_report["prediction"] = current_merged["prediction"].values[:len(current_for_report)]

    report = Report(metrics=metrics)

    # Run report on filtered data (modifies report in place)
    report.run(reference_data=reference_for_report, current_data=current_for_report)

    # Save as HTML
    report.save_html(output_path)
    logger.info(f"✓ Report saved to {output_path}")

    # Also save as JSON for programmatic access
    json_path = output_path.replace(".html", ".json")
    report.save_json(json_path)
    logger.info(f"✓ JSON report saved to {json_path}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Generate Evidently report for production model monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Production monitoring: Compare training data vs live production data
  python scripts/generate_evidently_report.py \\
    --report_type production_monitoring \\
    --reference data/processed/features_labeled.parquet \\
    --current data/processed/features_live.parquet \\
    --predictions logs/predictions \\
    --time_window 24

  # Load predictions from Kafka
  python scripts/generate_evidently_report.py \\
    --report_type production_monitoring \\
    --reference data/processed/features_labeled.parquet \\
    --current data/processed/features_live.parquet \\
    --kafka localhost:9092 \\
    --time_window 24

  # Data drift: Compare early vs late production data
  python scripts/generate_evidently_report.py \\
    --report_type data_drift \\
    --features data/processed/features_live.parquet \\
    --time_window 48
        """
    )
    parser.add_argument(
        "--report_type",
        choices=["production_monitoring", "data_drift", "train_test"],
        default="production_monitoring",
        help="Type of report: production_monitoring (reference vs live), "
             "data_drift (early/late), or train_test (train/test)",
    )
    
    # Reference data (for production_monitoring)
    parser.add_argument(
        "--reference",
        help="Path to reference (training) features parquet file",
    )
    
    # Current/live data
    parser.add_argument(
        "--current",
        help="Path to current (live production) features parquet file",
    )
    parser.add_argument(
        "--features",
        help="Path to features parquet file (for data_drift or train_test)",
    )
    
    # Predictions
    parser.add_argument(
        "--predictions",
        help="Path to predictions directory (logs/predictions) or 'kafka' to load from Kafka",
    )
    parser.add_argument(
        "--kafka",
        default="localhost:9092",
        help="Kafka bootstrap servers (if loading predictions from Kafka)",
    )
    parser.add_argument(
        "--kafka_topic",
        default="predictions.log",
        help="Kafka topic for predictions (default: predictions.log)",
    )
    
    # Time window
    parser.add_argument(
        "--time_window",
        type=int,
        help="Only analyze data from last N hours (optional)",
    )
    
    parser.add_argument(
        "--output",
        default="reports/evidently/production_monitoring_report.html",
        help="Output path for HTML report",
    )
    parser.add_argument(
        "--split_ratio",
        type=float,
        default=0.5,
        help="Fraction of data to use as reference for data_drift report (default 0.5)",
    )
    parser.add_argument(
        "--no_prediction_drift",
        action="store_true",
        help="Disable prediction drift metrics even if predictions are available",
    )

    args = parser.parse_args()

    # Load data based on report type
    if args.report_type == "production_monitoring":
        # Production monitoring: reference (training) vs current (live)
        if not args.reference:
            logger.error("--reference required for production_monitoring report")
            return 1
        if not args.current:
            logger.error("--current required for production_monitoring report")
            return 1
        
        logger.info("=" * 70)
        logger.info("PRODUCTION MONITORING REPORT")
        logger.info("Comparing reference (training) data vs live production data")
        logger.info("=" * 70)
        
        # Load reference data
        logger.info("Loading reference (training) data...")
        reference = pd.read_parquet(args.reference)
        if "timestamp" in reference.columns:
            reference["timestamp"] = pd.to_datetime(reference["timestamp"])
        
        # Load current production data
        logger.info("Loading current (live production) data...")
        current = load_live_features(args.current, args.time_window)
        
        # Load predictions if available
        reference_predictions = None
        current_predictions = None
        
        if args.predictions:
            logger.info("Loading predictions...")
            if args.predictions.lower() == "kafka":
                current_predictions = load_predictions_from_kafka_topic(
                    args.kafka, args.kafka_topic, args.time_window
                )
            else:
                current_predictions = load_predictions_from_files(
                    args.predictions, args.time_window
                )
        
        # Generate report
        generate_report(
            reference,
            current,
            args.output,
            reference_predictions=reference_predictions,
            current_predictions=current_predictions,
            include_prediction_drift=not args.no_prediction_drift,
        )
        
    elif args.report_type == "train_test":
        if not args.features:
            logger.error("--features required for train_test report")
            return 1
        
        logger.info("Generating train/test drift report...")
        reference, current = load_train_test_split(args.features)
        generate_report(reference, current, args.output, include_prediction_drift=False)
        
    else:  # data_drift
        if not args.features:
            logger.error("--features required for data_drift report")
            return 1
        
        logger.info("Generating early/late data drift report...")
        reference, current = load_and_split_data(args.features, args.split_ratio)
        generate_report(reference, current, args.output, include_prediction_drift=False)

    logger.info("=" * 70)
    logger.info("Done! Open the HTML report in your browser to view results.")
    logger.info(f"Report: {args.output}")
    logger.info("=" * 70)
    
    return 0


if __name__ == "__main__":
    main()
