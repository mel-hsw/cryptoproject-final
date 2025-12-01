"""
Baseline volatility detection model using rule-based approach.
This serves as the benchmark for ML models.

Updated to compute a composite volatility score from the same spread of
features used by train.py. The composite score is the mean of per-feature
z-scores (standardized using training data), and the z-score of that
composite is used for thresholding.
"""

import numpy as np
import pandas as pd
from typing import Dict, List
from sklearn.metrics import (
    precision_recall_fscore_support,
    roc_auc_score,
    average_precision_score,
    confusion_matrix,
)


class BaselineVolatilityDetector:
    """
    Rule-based baseline model for volatility spike detection.
    Uses a composite z-score across a set of volatility-related features.
    """

    DEFAULT_FEATURES = [
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

    def __init__(
        self, threshold: float = 2.0, feature_cols: List[str] = None, eps: float = 1e-8
    ):
        """
        Args:
            threshold: Z-score threshold for spike detection (default: 2.0)
            feature_cols: List of feature column names to use. If None, uses DEFAULT_FEATURES.
            eps: small epsilon to avoid division by zero
        """
        self.threshold = threshold
        self.feature_cols = (
            feature_cols if feature_cols is not None else list(self.DEFAULT_FEATURES)
        )
        self.eps = eps

        # Will be populated in fit()
        self.feature_means_: Dict[str, float] = {}
        self.feature_stds_: Dict[str, float] = {}
        self.composite_mean_: float = None
        self.composite_std_: float = None
        self.fitted_ = False

    def _validate_features_present(self, X: pd.DataFrame):
        missing = [c for c in self.feature_cols if c not in X.columns]
        if missing:
            raise ValueError(
                f"Missing required feature columns: {missing}. "
                f"Available columns: {X.columns.tolist()}"
            )

    def _compute_per_feature_stats(self, X: pd.DataFrame):
        """
        Compute mean and std for each selected feature using training data.
        """
        self.feature_means_ = {}
        self.feature_stds_ = {}
        for feat in self.feature_cols:
            col = X[feat].dropna()
            mean = float(col.mean()) if not col.empty else 0.0
            std = float(col.std()) if not col.empty else 0.0
            # guard against zero std
            if std < self.eps:
                std = self.eps
            self.feature_means_[feat] = mean
            self.feature_stds_[feat] = std

    def _compute_composite_from_X(self, X: pd.DataFrame) -> pd.Series:
        """
        Compute composite score (mean of per-feature z-scores) for rows in X.
        Missing feature values are filled with the training mean for that feature.
        """
        z_frames = []
        for feat in self.feature_cols:
            # Fill missing values with training mean (if fitted), else use column mean
            if feat in X.columns:
                col = X[feat].copy()
            else:
                # shouldn't happen if validated before
                col = pd.Series(np.nan, index=X.index)

            mean = self.feature_means_.get(
                feat, float(X[feat].mean() if feat in X.columns else 0.0)
            )
            std = self.feature_stds_.get(feat, self.eps)

            col_filled = col.fillna(mean)
            z = (col_filled - mean) / (std + self.eps)
            z_frames.append(z)

        # DataFrame where each column is a feature's z-score
        z_df = pd.concat(z_frames, axis=1)
        # composite score: mean of z-scores across features (axis=1)
        composite = z_df.mean(axis=1)
        return composite

    def fit(self, X: pd.DataFrame, y: pd.Series = None):
        """
        Compute historical statistics for z-score calculation based on the selected feature set.

        Args:
            X: Features dataframe containing all required features
            y: Target labels (not used for baseline, but kept for API consistency)
        """
        self._validate_features_present(X)
        self._compute_per_feature_stats(X)

        # Compute composite scores on training data using training per-feature stats
        composite = self._compute_composite_from_X(X).dropna()
        self.composite_mean_ = float(composite.mean()) if not composite.empty else 0.0
        self.composite_std_ = (
            float(composite.std()) if not composite.empty else self.eps
        )
        if self.composite_std_ < self.eps:
            self.composite_std_ = self.eps

        self.fitted_ = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """
        Predict volatility spikes using z-score threshold on composite score.

        Args:
            X: Features dataframe

        Returns:
            Binary predictions (1 = spike, 0 = normal)
        """
        if not self.fitted_:
            raise ValueError("Model must be fitted before prediction")

        self._validate_features_present(X)

        composite = self._compute_composite_from_X(X)
        # z-score relative to training composite distribution
        z_scores = (composite - self.composite_mean_) / (self.composite_std_ + self.eps)
        predictions = (z_scores >= self.threshold).astype(int)
        return predictions.values

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """
        Return probability-like scores based on composite z-score distance.

        Args:
            X: Features dataframe

        Returns:
            Array of shape (n_samples, 2) with probabilities for [normal, spike]
        """
        if not self.fitted_:
            raise ValueError("Model must be fitted before prediction")

        self._validate_features_present(X)

        composite = self._compute_composite_from_X(X)
        z_scores = (composite - self.composite_mean_) / (self.composite_std_ + self.eps)

        # Convert z-scores to pseudo-probabilities using sigmoid
        spike_proba = 1 / (1 + np.exp(-z_scores))
        normal_proba = 1 - spike_proba

        return np.column_stack([normal_proba, spike_proba])

    def evaluate(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, float]:
        """
        Evaluate model performance.

        Args:
            X: Features dataframe
            y: True labels

        Returns:
            Dictionary of evaluation metrics
        """
        y_pred = self.predict(X)
        y_proba = self.predict_proba(X)[:, 1]

        # Compute metrics
        precision, recall, f1, _ = precision_recall_fscore_support(
            y, y_pred, average="binary", zero_division=0
        )

        try:
            roc_auc = roc_auc_score(y, y_proba)
        except ValueError:
            roc_auc = 0.0

        try:
            pr_auc = average_precision_score(y, y_proba)
        except ValueError:
            pr_auc = 0.0

        # Robust confusion matrix handling (in case only one class present)
        tn = fp = fn = tp = 0
        cm = confusion_matrix(y, y_pred)
        if cm.size == 4:
            tn, fp, fn, tp = cm.ravel()
        elif cm.shape == (1, 1):
            # Only one class present in both y and y_pred
            if y.unique()[0] == 0:
                tn = int(cm[0, 0])
            else:
                tp = int(cm[0, 0])
        elif cm.shape == (1, 2):
            # Only one true label present (0), predicted both
            tn, fp = int(cm[0, 0]), int(cm[0, 1])
        elif cm.shape == (2, 1):
            # Only one predicted label present
            tn, fn = int(cm[0, 0]), int(cm[1, 0])

        return {
            "precision": float(precision),
            "recall": float(recall),
            "f1_score": float(f1),
            "roc_auc": float(roc_auc),
            "pr_auc": float(pr_auc),
            "true_positives": int(tp),
            "false_positives": int(fp),
            "true_negatives": int(tn),
            "false_negatives": int(fn),
            "threshold": self.threshold,
            "features_used": list(self.feature_cols),
        }
