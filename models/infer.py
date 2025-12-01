"""
Real-time inference for volatility detection.
Supports scoring in < 2x real-time for 60-second prediction windows.
"""

import argparse
import pickle
import time
from pathlib import Path
from typing import Dict

import pandas as pd

# Import feature preparation logic from train.py for consistency
# Handle imports for both module and script execution
import sys

# Add models directory to path to allow importing train module
models_dir = Path(__file__).parent
if str(models_dir) not in sys.path:
    sys.path.insert(0, str(models_dir))

from train import prepare_features  # noqa: E402


class VolatilityPredictor:
    """
    Wrapper for real-time volatility predictions.
    """

    def __init__(self, model_path: str):
        """
        Load trained model for inference.

        Args:
            model_path: Path to pickled model file
        """
        self.model_path = Path(model_path)
        with open(self.model_path, "rb") as f:
            self.model = pickle.load(f)

        # Load threshold metadata if available
        threshold_path = self.model_path.parent / "threshold_metadata.json"
        if threshold_path.exists():
            import json

            with open(threshold_path, "r") as f:
                threshold_metadata = json.load(f)
                # Use threshold_used if available (preferred), otherwise fall back to threshold_10pct or optimal_threshold
                self.threshold = threshold_metadata.get(
                    "threshold_used",
                    threshold_metadata.get(
                        "threshold_10pct",
                        threshold_metadata.get("optimal_threshold", 0.5),
                    ),
                )
        else:
            # Default threshold (fallback)
            self.threshold = 0.5

        self.prediction_count = 0
        self.total_inference_time = 0.0

    def predict(self, features: pd.DataFrame) -> Dict:
        """
        Make prediction on new data.

        Args:
            features: DataFrame with feature columns

        Returns:
            Dictionary with prediction, probability, and timing info
        """
        start_time = time.time()

        # Make prediction
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(features)[:, 1]
        else:
            proba = self.model.predict(features)

        # Use optimized threshold instead of default 0.5
        prediction = (proba >= self.threshold).astype(int)

        inference_time = time.time() - start_time
        self.prediction_count += 1
        self.total_inference_time += inference_time

        return {
            "prediction": int(prediction[0]) if len(prediction) > 0 else 0,
            "probability": float(proba[0]) if len(proba) > 0 else 0.0,
            "inference_time_ms": inference_time * 1000,
            "alert": bool(prediction[0]) if len(prediction) > 0 else False,
        }

    def predict_batch(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Batch prediction for multiple samples.

        Args:
            features: DataFrame with feature columns

        Returns:
            DataFrame with predictions and probabilities
        """
        start_time = time.time()

        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(features)[:, 1]
        else:
            proba = self.model.predict(features)

        # Use optimized threshold instead of default 0.5
        predictions = (proba >= self.threshold).astype(int)

        inference_time = time.time() - start_time
        self.prediction_count += len(features)
        self.total_inference_time += inference_time

        results = pd.DataFrame(
            {
                "prediction": predictions,
                "probability": proba,
                "alert": predictions.astype(bool),
            }
        )

        return results

    def get_stats(self) -> Dict:
        """Get inference statistics."""
        avg_time = (
            self.total_inference_time / self.prediction_count * 1000
            if self.prediction_count > 0
            else 0
        )

        return {
            "total_predictions": self.prediction_count,
            "total_time_seconds": self.total_inference_time,
            "avg_inference_time_ms": avg_time,
            "throughput_per_second": (
                self.prediction_count / self.total_inference_time
                if self.total_inference_time > 0
                else 0
            ),
        }


def prepare_features_for_inference(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare features for inference using the same logic as training.
    Supports both naming conventions (expected and actual feature names).

    Args:
        df: DataFrame with feature columns

    Returns:
        DataFrame with prepared features (same columns as used in training)
    """
    # Use the same prepare_features function from train.py
    # If volatility_spike exists, use it (for evaluation), otherwise create dummy
    if "volatility_spike" not in df.columns:
        df = df.copy()
        df["volatility_spike"] = 0  # Dummy column, won't be used

    X, _ = prepare_features(df)
    return X


def benchmark_inference(model_path: str, features_path: str, n_samples: int = 1000):
    """
    Benchmark inference performance to verify < 2x real-time requirement.

    For 60-second prediction windows, inference must complete in < 120 seconds.

    Args:
        model_path: Path to model
        features_path: Path to test features
        n_samples: Number of samples to benchmark
    """
    print("\n=== Inference Benchmark ===")
    print(f"Model: {model_path}")
    print("Requirement: < 120 seconds for 60-second prediction window")
    print(f"Testing with {n_samples} samples...\n")

    # Load model and data
    predictor = VolatilityPredictor(model_path)
    df = pd.read_parquet(features_path)

    # Take random sample
    if len(df) > n_samples:
        df = df.sample(n=n_samples, random_state=42)

    # Prepare features using the same logic as training
    X = prepare_features_for_inference(df)
    print(f"Using {len(X.columns)} features: {list(X.columns)}")

    # Single prediction benchmark
    print("Single prediction test:")
    single_start = time.time()
    result = predictor.predict(X.iloc[[0]])
    single_time = time.time() - single_start
    print(f"  Time: {single_time * 1000:.2f} ms")
    print(f"  Prediction: {result['prediction']} (prob: {result['probability']:.3f})")

    # Batch prediction benchmark
    print(f"\nBatch prediction test ({len(X)} samples):")
    batch_start = time.time()
    results = predictor.predict_batch(X)
    batch_time = time.time() - batch_start

    avg_time_per_sample = (batch_time / len(X)) * 1000

    print(f"  Total time: {batch_time:.3f} seconds")
    print(f"  Avg per sample: {avg_time_per_sample:.2f} ms")
    print(f"  Throughput: {len(X) / batch_time:.1f} predictions/second")

    # Check real-time requirement
    real_time_window = 60  # seconds
    max_allowed_time = 2 * real_time_window  # 2x real-time = 120 seconds

    print("\n=== Real-Time Performance Check ===")
    print(f"Prediction window: {real_time_window} seconds")
    print(f"Maximum allowed inference time: {max_allowed_time} seconds")
    print(f"Actual batch time: {batch_time:.3f} seconds")

    if batch_time < max_allowed_time:
        print(
            f"✓ PASSED: Inference is {max_allowed_time / batch_time:.1f}x faster than requirement"
        )
    else:
        print(
            f"✗ FAILED: Inference is too slow ({batch_time / max_allowed_time:.1f}x requirement)"
        )

    # Alert rate
    alert_rate = results["alert"].mean()
    print(f"\nAlert rate: {alert_rate:.2%} ({results['alert'].sum()} / {len(results)})")

    return predictor.get_stats()


def run_live_inference(model_path: str, features_path: str):
    """
    Simulate live inference on streaming data.

    Args:
        model_path: Path to model
        features_path: Path to features (simulating streaming data)
    """
    print("\n=== Live Inference Simulation ===")
    print(f"Model: {model_path}\n")

    predictor = VolatilityPredictor(model_path)
    df = pd.read_parquet(features_path)
    df = df.sort_values("timestamp")

    # Prepare features using the same logic as training
    X = prepare_features_for_inference(df)
    print(f"Using {len(X.columns)} features: {list(X.columns)}\n")

    print("Streaming predictions (showing first 10):")
    print(
        f"{'Timestamp':<25} {'Prediction':<12} {'Probability':<12} {'Alert':<8} {'Time (ms)':<10}"
    )
    print("-" * 80)

    for i, (idx, row) in enumerate(df.iterrows()):
        if i >= 10:  # Show first 10
            break

        # Get features for this row using the same column selection
        row_df = df.iloc[[i]]
        features = prepare_features_for_inference(row_df)
        result = predictor.predict(features)

        timestamp = row["timestamp"]
        alert_symbol = "🚨" if result["alert"] else "  "

        print(
            f"{timestamp} {result['prediction']:<12} {result['probability']:<12.3f} "
            f"{alert_symbol:<8} {result['inference_time_ms']:<10.2f}"
        )

        # Simulate streaming delay
        time.sleep(0.01)

    print("\n...")
    print(f"Processed {len(df)} total samples")

    stats = predictor.get_stats()
    print("\n=== Statistics ===")
    print(f"Total predictions: {stats['total_predictions']}")
    print(f"Average inference time: {stats['avg_inference_time_ms']:.2f} ms")
    print(f"Throughput: {stats['throughput_per_second']:.1f} predictions/second")


def main():
    parser = argparse.ArgumentParser(
        description="Run inference for volatility detection"
    )
    parser.add_argument("--model", required=True, help="Path to model pickle file")
    parser.add_argument(
        "--features",
        default="data/processed/features.parquet",
        help="Path to features parquet file",
    )
    parser.add_argument(
        "--mode",
        choices=["benchmark", "live"],
        default="benchmark",
        help="Inference mode",
    )
    parser.add_argument(
        "--n-samples", type=int, default=1000, help="Number of samples for benchmark"
    )

    args = parser.parse_args()

    if not Path(args.model).exists():
        print(f"Error: Model not found at {args.model}")
        print("\nAvailable models:")
        models_dir = Path("models/artifacts")
        if models_dir.exists():
            for model_file in models_dir.rglob("*.pkl"):
                print(f"  {model_file}")
        return

    if not Path(args.features).exists():
        print(f"Error: Features not found at {args.features}")
        return

    if args.mode == "benchmark":
        benchmark_inference(args.model, args.features, args.n_samples)
    else:
        run_live_inference(args.model, args.features)


if __name__ == "__main__":
    main()
