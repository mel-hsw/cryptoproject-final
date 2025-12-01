"""
Generate Evidently report to monitor data quality and drift.
Supports two report types:
1. data_drift: Compares early vs late windows of collected data
2. train_test: Compares train vs test datasets (using same split as training)
"""

import argparse
import pandas as pd
from pathlib import Path

from evidently.report import Report
from evidently.metrics import (
    DatasetDriftMetric,
    ColumnDriftMetric,
    DatasetMissingValuesMetric,
    DatasetSummaryMetric,
)

import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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
    Same logic as models/train.py for consistency.

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
    Select features for drift analysis using the same logic as train.py/infer.py.
    This ensures we analyze the same features that are actually used in the model.

    Uses reduced feature set (10 features) to minimize multicollinearity:
    - Removed perfectly correlated duplicates (r=1.0)
    - Keeps diverse time windows and feature types

    Args:
        df: DataFrame with features

    Returns:
        List of column names to analyze
    """
    # Reduced feature set (same as train.py) - removes perfect correlations
    priority_features = [
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


def generate_report(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    output_path: str = "reports/evidently/data_drift_report.html",
):
    """
    Generate Evidently report comparing reference and current data.
    Only analyzes the same feature columns used in training/inference.

    Args:
        reference_df: Reference (early) dataset
        current_df: Current (late) dataset
        output_path: Where to save the HTML report
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

    report = Report(
        metrics=[
            DatasetSummaryMetric(),
            DatasetMissingValuesMetric(),
            DatasetDriftMetric(),
            *drift_metrics,  # Unpack list of ColumnDriftMetric metrics
        ]
    )

    # Run report on filtered data (modifies report in place)
    report.run(reference_data=reference_filtered, current_data=current_filtered)

    # Save as HTML
    report.save_html(output_path)
    logger.info(f"✓ Report saved to {output_path}")

    # Also save as JSON for programmatic access
    json_path = output_path.replace(".html", ".json")
    report.save_json(json_path)
    logger.info(f"✓ JSON report saved to {json_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="Generate Evidently data drift report")
    parser.add_argument(
        "--features",
        default="data/processed/features.parquet",
        help="Path to features parquet file",
    )
    parser.add_argument(
        "--output",
        default="reports/evidently/data_drift_report.html",
        help="Output path for HTML report",
    )
    parser.add_argument(
        "--report_type",
        choices=["data_drift", "train_test"],
        default="data_drift",
        help="Type of report: data_drift (early/late) or train_test (train/test)",
    )
    parser.add_argument(
        "--split_ratio",
        type=float,
        default=0.5,
        help="Fraction of data to use as reference for data_drift report (default 0.5)",
    )

    args = parser.parse_args()

    # Load and split data based on report type
    if args.report_type == "train_test":
        logger.info("Generating train/test drift report...")
        reference, current = load_train_test_split(args.features)
    else:
        logger.info("Generating early/late data drift report...")
        reference, current = load_and_split_data(args.features, args.split_ratio)

    # Generate report
    generate_report(reference, current, args.output)

    logger.info("Done! Open the HTML report in your browser to view results.")


if __name__ == "__main__":
    main()
