"""
Feature preparation and model inference.

This is the single source of truth for MODEL_FEATURES, feature preparation, and inference.
Provides consistent feature selection and model prediction across the entire pipeline.
"""

import pickle
import time
from pathlib import Path
from typing import Tuple, List, Dict

import pandas as pd

# MODEL_FEATURES - Top 10 features by Random Forest importance (from feature_analysis.py)
# PR-AUC: 0.9006 ± 0.0095 (cross-validated)
# This is the single source of truth for all feature selection
MODEL_FEATURES = [
    "log_return_300s",  # 0.1229 - Log return over 300s window
    "spread_mean_300s",  # 0.1059 - 300s spread mean (liquidity)
    "trade_intensity_300s",  # 0.1010 - 300s trade intensity (activity)
    "order_book_imbalance_300s",  # 0.0951 - 300s OBI (microstructure)
    "spread_mean_60s",  # 0.0833 - 60s spread mean
    "order_book_imbalance_60s",  # 0.0698 - 60s OBI
    "price_velocity_300s",  # 0.0696 - 300s price velocity (momentum)
    "realized_volatility_300s",  # 0.0636 - 300s realized volatility (target proxy)
    "order_book_imbalance_30s",  # 0.0430 - 30s OBI
    "realized_volatility_60s",  # 0.0404 - 60s realized volatility
]

# Baseline model uses the same features as the ML model
BASELINE_FEATURES = MODEL_FEATURES


def select_features(df: pd.DataFrame, feature_list: List[str] = None) -> pd.DataFrame:
    """
    Select and prepare features from a dataframe.

    Args:
        df: DataFrame with feature columns
        feature_list: List of feature names to select. If None, uses MODEL_FEATURES.

    Returns:
        DataFrame with selected features (NaN filled with 0)
    """
    if feature_list is None:
        feature_list = MODEL_FEATURES

    # Select available features
    available_features = [feat for feat in feature_list if feat in df.columns]

    if not available_features:
        raise ValueError(
            f"No matching feature columns found in dataframe. "
            f"Expected: {feature_list}. "
            f"Available: {df.columns.tolist()}"
        )

    if len(available_features) < len(feature_list):
        missing = set(feature_list) - set(available_features)
        print(f"⚠ Warning: Some expected features missing: {missing}")
        print(
            f"⚠ Warning: Using {len(available_features)} available features: {available_features}"
        )

    # Select features and fill NaN values with 0
    X = df[available_features].copy()
    X = X.fillna(0)

    return X


def prepare_features_for_training(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Prepare features for training (includes target variable).

    Uses top 10 features based on Random Forest feature importance analysis:
    - Selected from feature_analysis.py results
    - PR-AUC: 0.9006 ± 0.0095 (cross-validated)
    - Focuses on most predictive features across different time windows

    Args:
        df: Features dataframe with 'volatility_spike' column

    Returns:
        Tuple of (X, y) where X has MODEL_FEATURES and y is volatility_spike
    """
    if "volatility_spike" not in df.columns:
        raise ValueError("DataFrame must have 'volatility_spike' column for training")

    X = select_features(df, MODEL_FEATURES)
    y = df["volatility_spike"].copy()

    return X, y


def prepare_features_for_inference(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare features for inference (no target variable needed).

    The featurizer outputs exactly MODEL_FEATURES, so this function
    validates that all required features are present and returns them.

    Args:
        df: DataFrame with feature columns (should contain MODEL_FEATURES)

    Returns:
        DataFrame with MODEL_FEATURES selected and prepared (NaN filled with 0)
    """
    return select_features(df, MODEL_FEATURES)


def prepare_baseline_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare features for baseline model.

    Args:
        df: DataFrame with feature columns

    Returns:
        DataFrame with BASELINE_FEATURES selected and prepared
    """
    return select_features(df, BASELINE_FEATURES)


class VolatilityPredictor:
    """
    Wrapper for real-time volatility predictions.
    Loads trained models and makes predictions on prepared features.
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
                        threshold_metadata.get("optimal_threshold", 0.05),
                    ),
                )
        else:
            # Default threshold (fallback)
            self.threshold = 0.05

        self.prediction_count = 0
        self.total_inference_time = 0.0

    def predict(self, features: pd.DataFrame) -> Dict:
        """
        Make prediction on new data.

        Args:
            features: DataFrame with feature columns (should have MODEL_FEATURES)

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
