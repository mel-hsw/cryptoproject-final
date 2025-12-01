"""
Replay script: Re-process raw data through feature pipeline to verify reproducibility.
Uses the same FeatureComputer logic as the featurizer, with optional chunk-aware label creation.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
import pandas as pd

# Add parent directory to path to import features module
sys.path.insert(0, str(Path(__file__).parent.parent))

from features.featurizer import FeatureComputer, FeaturePipeline  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_raw_data(raw_paths: list) -> list:
    """
    Load raw tick data from NDJSON files.

    Args:
        raw_paths: List of file paths or glob patterns

    Returns:
        List of tick dictionaries
    """
    ticks = []

    for pattern in raw_paths:
        # Handle glob patterns
        if "*" in pattern:
            files = Path(".").glob(pattern)
        else:
            files = [Path(pattern)]

        for filepath in files:
            if not filepath.exists():
                logger.warning(f"File not found: {filepath}")
                continue

            logger.info(f"Loading {filepath}")

            with open(filepath, "r") as f:
                for line in f:
                    try:
                        tick = json.loads(line.strip())
                        ticks.append(tick)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse line: {e}")
                        continue

    logger.info(f"Loaded {len(ticks)} total ticks")
    return ticks


def replay_features(ticks: list, window_sizes: list = [30, 60, 300]) -> pd.DataFrame:
    """
    Replay ticks through feature computation (same logic as featurizer).

    Args:
        ticks: List of tick dictionaries (should be sorted by timestamp)
        window_sizes: Window sizes in seconds

    Returns:
        DataFrame of computed features
    """
    feature_computer = FeatureComputer(window_sizes=window_sizes)
    features_list = []

    logger.info("Computing features...")
    logger.info(f"Using window sizes: {window_sizes}s")

    for i, tick in enumerate(ticks):
        # Add tick to buffer
        feature_computer.add_tick(tick)

        # Compute features (same logic as FeaturePipeline.process_message)
        features = feature_computer.compute_features(tick)
        features_list.append(features)

        if (i + 1) % 1000 == 0:
            logger.info(
                f"Processed {i + 1}/{len(ticks)} ticks ({100*(i+1)/len(ticks):.1f}%)"
            )

    logger.info(f"✓ Computed features for {len(features_list)} ticks")

    # Convert to DataFrame
    df = pd.DataFrame(features_list)

    # Ensure timestamp is datetime (important for gap detection and label creation)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])

    return df


def compare_outputs(
    replay_df: pd.DataFrame, original_file: str, compare_labels: bool = False
):
    """
    Compare replay output with original live features.

    Args:
        replay_df: Features from replay
        original_file: Path to original features parquet
        compare_labels: Whether to compare volatility_spike labels if present
    """
    if not Path(original_file).exists():
        logger.warning(f"Original file not found: {original_file}")
        logger.info("Cannot compare outputs.")
        return

    logger.info(f"Loading original features from {original_file}")
    original_df = pd.read_parquet(original_file)

    # Ensure timestamps are datetime for proper comparison
    if "timestamp" in original_df.columns:
        original_df["timestamp"] = pd.to_datetime(original_df["timestamp"])
    if "timestamp" in replay_df.columns:
        replay_df["timestamp"] = pd.to_datetime(replay_df["timestamp"])

    # Sort both by timestamp for comparison
    original_df = original_df.sort_values("timestamp").reset_index(drop=True)
    replay_df = replay_df.sort_values("timestamp").reset_index(drop=True)

    # Basic comparison
    logger.info("\nComparison:")
    logger.info(f"  Replay rows: {len(replay_df)}")
    logger.info(f"  Original rows: {len(original_df)}")

    # Check column names
    replay_cols = set(replay_df.columns)
    original_cols = set(original_df.columns)

    # Exclude label columns from column comparison if not comparing labels
    if not compare_labels:
        label_cols = {
            "volatility_spike",
            "label",
            "future_volatility",
            "price_pct_change",
            "chunk_id",
            "time_diff",
        }
        replay_cols = replay_cols - label_cols
        original_cols = original_cols - label_cols

    if replay_cols == original_cols:
        logger.info(f"  ✓ Column names match ({len(replay_cols)} columns)")
    else:
        logger.warning("  ✗ Column mismatch!")
        logger.warning(f"    Only in replay: {replay_cols - original_cols}")
        logger.warning(f"    Only in original: {original_cols - replay_cols}")

    # Compare numeric columns (within tolerance due to floating point)
    # Focus on feature columns, not labels
    feature_cols = [
        col
        for col in replay_df.columns
        if col
        not in [
            "volatility_spike",
            "label",
            "future_volatility",
            "price_pct_change",
            "chunk_id",
            "time_diff",
        ]
        and pd.api.types.is_numeric_dtype(replay_df[col])
    ]

    if len(replay_df) == len(original_df):
        matches = 0
        mismatches = 0

        for col in feature_cols:
            if col in original_df.columns:
                # Drop NaN for comparison
                replay_vals = replay_df[col].dropna()
                original_vals = original_df[col].dropna()

                if len(replay_vals) > 0 and len(original_vals) > 0:
                    # Check if values are close (within 1e-6)
                    if len(replay_vals) == len(original_vals):
                        close = (
                            pd.Series(replay_vals.values)
                            .sub(original_vals.values)
                            .abs()
                            < 1e-6
                        )
                        match_pct = close.sum() / len(close) * 100

                        if match_pct >= 99.9:
                            logger.info(f"  ✓ {col}: {match_pct:.2f}% match")
                            matches += 1
                        else:
                            logger.warning(f"  ⚠ {col}: {match_pct:.2f}% match")
                            mismatches += 1

        logger.info(
            f"\n  Summary: {matches} features match, {mismatches} features differ"
        )

        # Compare labels if both have them and compare_labels is True
        if (
            compare_labels
            and "volatility_spike" in replay_df.columns
            and "volatility_spike" in original_df.columns
        ):
            label_match = (
                replay_df["volatility_spike"] == original_df["volatility_spike"]
            ).sum()
            label_match_pct = label_match / len(replay_df) * 100
            logger.info("\n  Label comparison:")
            logger.info(
                f"    volatility_spike match: {label_match_pct:.2f}% ({label_match}/{len(replay_df)})"
            )
    else:
        logger.warning("  Cannot compare values: different number of rows")
        logger.warning(f"    Replay: {len(replay_df)}, Original: {len(original_df)}")


def main():
    parser = argparse.ArgumentParser(
        description="Replay raw data through feature pipeline to verify reproducibility. "
        "Uses the same FeatureComputer logic as the featurizer."
    )
    parser.add_argument(
        "--raw",
        nargs="+",
        required=True,
        help="Path(s) to raw NDJSON files (supports glob patterns)",
    )
    parser.add_argument(
        "--out",
        default="data/processed/features_replay.parquet",
        help="Output parquet file for replayed features",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[30, 60, 300],
        help="Window sizes in seconds (default: 30 60 300)",
    )
    parser.add_argument(
        "--compare",
        default="data/processed/features.parquet",
        help="Original features file to compare against",
    )
    parser.add_argument(
        "--add-labels",
        action="store_true",
        default=False,
        help="Add volatility_spike labels using chunk-aware logic (same as featurizer)",
    )
    parser.add_argument(
        "--label-threshold-percentile",
        type=int,
        default=90,
        help="Percentile to use as threshold for labels (default: 90)",
    )
    parser.add_argument(
        "--label-gap-threshold-seconds",
        type=int,
        default=300,
        help="Time gap (seconds) that indicates a new data chunk (default: 300 = 5 min)",
    )
    parser.add_argument(
        "--compare-labels",
        action="store_true",
        default=False,
        help="Compare volatility_spike labels when comparing with original file",
    )

    args = parser.parse_args()

    # Create output directory
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    # Load raw data
    ticks = load_raw_data(args.raw)

    if not ticks:
        logger.error("No ticks loaded. Exiting.")
        return

    # Sort ticks by timestamp (important for gap detection and label creation)
    try:
        ticks = sorted(
            ticks, key=lambda t: pd.to_datetime(t.get("timestamp", t.get("time", 0)))
        )
        logger.info("Sorted ticks by timestamp")
    except Exception as e:
        logger.warning(f"Could not sort ticks by timestamp: {e}")

    # Replay features (same logic as featurizer)
    features_df = replay_features(ticks, window_sizes=args.windows)

    # Add labels if requested (using chunk-aware logic from featurizer)
    if args.add_labels:
        logger.info("Adding volatility_spike labels using chunk-aware logic...")
        features_df = FeaturePipeline._add_labels_to_dataframe(
            features_df,
            threshold_percentile=args.label_threshold_percentile,
            gap_threshold_seconds=args.label_gap_threshold_seconds,
        )
        logger.info(f"Labels added: {len(features_df)} rows")

        if "volatility_spike" in features_df.columns:
            spike_rate = features_df["volatility_spike"].mean()
            spike_count = features_df["volatility_spike"].sum()
            logger.info(
                f"  Spike rate: {spike_rate:.2%} ({spike_count} spikes out of {len(features_df)} total)"
            )

    # Save replayed features
    logger.info(f"Saving replayed features to {args.out}")
    features_df.to_parquet(args.out, index=False)
    logger.info(f"✓ Saved {len(features_df)} rows")

    # Compare with original if available
    if args.compare:
        compare_outputs(features_df, args.compare, compare_labels=args.compare_labels)


if __name__ == "__main__":
    main()
