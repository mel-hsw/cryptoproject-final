"""
Training pipeline for volatility detection models.
Supports baseline (z-score) and ML models (Logistic Regression, XGBoost, Random Forest).
Logs everything to MLflow.
"""

import argparse
import json
import pickle
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import mlflow
import mlflow.sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    precision_recall_fscore_support,
    roc_auc_score,
    average_precision_score,
    confusion_matrix,
    precision_recall_curve,
    roc_curve,
)
import matplotlib.pyplot as plt

from baseline import BaselineVolatilityDetector

# Try to import XGBoost (optional)
try:
    import xgboost as xgb

    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    print("XGBoost not available. Install with: pip install xgboost")

from sklearn.ensemble import RandomForestClassifier


def load_and_split_data(
    features_path: str,
    val_size: float = 0.15,
    test_size: float = 0.15,
    split_method: str = "time_based",
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Load features and split into train/val/test.

    Args:
        features_path: Path to features parquet file
        val_size: Fraction for validation set
        test_size: Fraction for test set
        split_method: "time_based" or "stratified" (default: "time_based")

    Returns:
        Tuple of (train_df, val_df, test_df)
    """
    df = pd.read_parquet(features_path)

    # Validate required columns
    if "timestamp" not in df.columns:
        raise ValueError(
            f"Missing required column 'timestamp' in {features_path}. "
            f"Available columns: {df.columns.tolist()}"
        )

    if "volatility_spike" not in df.columns:
        raise ValueError(
            f"Missing required column 'volatility_spike' in {features_path}.\n"
            f"Available columns: {df.columns.tolist()}\n\n"
            f"This file appears to be unlabeled. Please add labels using:\n"
            f"  python scripts/add_labels.py --features {features_path}\n\n"
            f"Or use a labeled file (e.g., features_labeled.parquet)"
        )

    df = df.sort_values("timestamp").reset_index(drop=True)

    if split_method == "stratified":
        # Stratified split to maintain balanced spike rates
        from sklearn.model_selection import train_test_split

        temp_size = val_size + test_size

        # First split: train vs (val+test)
        train_df, temp_df = train_test_split(
            df, test_size=temp_size, stratify=df["volatility_spike"], random_state=42
        )

        # Second split: val vs test
        val_ratio = val_size / temp_size
        val_df, test_df = train_test_split(
            temp_df,
            test_size=(1 - val_ratio),
            stratify=temp_df["volatility_spike"],
            random_state=42,
        )

        print("\nStratified Split (balanced spike rates):")
        print(
            f"  Train: {len(train_df)} samples ({train_df['volatility_spike'].mean():.2%} spikes)"
        )
        print(
            f"  Val:   {len(val_df)} samples ({val_df['volatility_spike'].mean():.2%} spikes)"
        )
        print(
            f"  Test:  {len(test_df)} samples ({test_df['volatility_spike'].mean():.2%} spikes)"
        )

    else:
        # Time-based split (original method)
        n = len(df)
        train_end = int(n * (1 - val_size - test_size))
        val_end = int(n * (1 - test_size))

        train_df = df.iloc[:train_end].copy()
        val_df = df.iloc[train_end:val_end].copy()
        test_df = df.iloc[val_end:].copy()

        print("\nTime-based Split:")
        print(
            f"  Train: {len(train_df)} samples ({train_df['volatility_spike'].mean():.2%} spikes)"
        )
        print(
            f"  Val:   {len(val_df)} samples ({val_df['volatility_spike'].mean():.2%} spikes)"
        )
        print(
            f"  Test:  {len(test_df)} samples ({test_df['volatility_spike'].mean():.2%} spikes)"
        )

    return train_df, val_df, test_df


def prepare_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Extract feature columns and target variable.

    Uses top 10 features based on Random Forest feature importance analysis:
    - Selected from feature_analysis.py results
    - PR-AUC: 0.9006 ± 0.0095 (cross-validated)
    - Focuses on most predictive features across different time windows

    Args:
        df: Features dataframe

    Returns:
        Tuple of (X, y)
    """
    # Top 10 features by Random Forest importance (from feature_analysis.py)
    priority_features = [
        # Top features (by importance score)
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

    # Select available features
    available_cols = [col for col in priority_features if col in df.columns]

    if not available_cols:
        raise ValueError(
            "No matching feature columns found in dataframe. "
            f"Available columns: {df.columns.tolist()}"
        )

    if len(available_cols) < len(priority_features):
        missing = set(priority_features) - set(available_cols)
        print(f"⚠ Warning: Some expected features missing: {missing}")
        print(
            f"⚠ Warning: Using {len(available_cols)} available features: {available_cols}"
        )

    X = df[available_cols].copy()
    y = df["volatility_spike"].copy()

    # Fill NaN values with 0
    X = X.fillna(0)

    return X, y


def compute_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, y_proba: np.ndarray
) -> Dict[str, float]:
    """
    Compute comprehensive evaluation metrics.

    Args:
        y_true: True labels
        y_pred: Binary predictions
        y_proba: Prediction probabilities

    Returns:
        Dictionary of metrics
    """
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", zero_division=0
    )

    try:
        roc_auc = roc_auc_score(y_true, y_proba)
    except ValueError:
        roc_auc = 0.0

    try:
        pr_auc = average_precision_score(y_true, y_proba)
    except ValueError:
        pr_auc = 0.0

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()

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
        "accuracy": float((tp + tn) / (tp + tn + fp + fn)),
    }


def plot_pr_curve(y_true: np.ndarray, y_proba: np.ndarray, save_path: Path):
    """Generate and save Precision-Recall curve."""
    precision, recall, _ = precision_recall_curve(y_true, y_proba)
    pr_auc = average_precision_score(y_true, y_proba)

    plt.figure(figsize=(8, 6))
    plt.plot(recall, precision, label=f"PR-AUC = {pr_auc:.3f}")
    plt.xlabel("Recall")
    plt.ylabel("Precision")
    plt.title("Precision-Recall Curve")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path)
    plt.close()


def plot_roc_curve(y_true: np.ndarray, y_proba: np.ndarray, save_path: Path):
    """Generate and save ROC curve."""
    fpr, tpr, _ = roc_curve(y_true, y_proba)
    roc_auc = roc_auc_score(y_true, y_proba)

    plt.figure(figsize=(8, 6))
    plt.plot(fpr, tpr, label=f"ROC-AUC = {roc_auc:.3f}")
    plt.plot([0, 1], [0, 1], "k--", label="Random")
    plt.xlabel("False Positive Rate")
    plt.ylabel("True Positive Rate")
    plt.title("ROC Curve")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path)
    plt.close()


def train_baseline(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    threshold: float = 2.0,
) -> Dict:
    """Train baseline z-score model using composite z-score across 8 features."""
    print("\n=== Training Baseline Model ===")

    X_train, y_train = prepare_features(train_df)
    X_val, y_val = prepare_features(val_df)
    X_test, y_test = prepare_features(test_df)

    # Use all 8 features for composite z-score baseline
    # These match BaselineVolatilityDetector.DEFAULT_FEATURES
    baseline_features = BaselineVolatilityDetector.DEFAULT_FEATURES

    # Filter to only include features that are available
    available_features = [f for f in baseline_features if f in X_train.columns]

    if not available_features:
        raise ValueError(
            f"None of the required baseline features found. "
            f"Expected: {baseline_features}. "
            f"Available: {X_train.columns.tolist()}"
        )

    if len(available_features) < len(baseline_features):
        missing = set(baseline_features) - set(available_features)
        print(f"⚠ Warning: Some baseline features missing: {missing}")
        print(
            f"   Using {len(available_features)} available features: {available_features}"
        )

    # Select only the baseline features
    X_train_base = X_train[available_features].copy()
    X_val_base = X_val[available_features].copy()
    X_test_base = X_test[available_features].copy()

    with mlflow.start_run(run_name="baseline_zscore"):
        # Log parameters
        mlflow.log_param("model_type", "baseline")
        mlflow.log_param("threshold", threshold)
        mlflow.log_param("train_samples", len(train_df))
        mlflow.log_param("val_samples", len(val_df))
        mlflow.log_param("test_samples", len(test_df))
        mlflow.log_param("features", available_features)
        mlflow.log_param("feature_count", len(available_features))

        # Train model with specified features (uses composite z-score)
        model = BaselineVolatilityDetector(
            threshold=threshold, feature_cols=available_features
        )
        model.fit(X_train_base, y_train)

        # Validation metrics
        y_val_pred = model.predict(X_val_base)
        y_val_proba = model.predict_proba(X_val_base)[:, 1]
        val_metrics = compute_metrics(y_val.values, y_val_pred, y_val_proba)

        # Test metrics
        y_test_pred = model.predict(X_test_base)
        y_test_proba = model.predict_proba(X_test_base)[:, 1]
        test_metrics = compute_metrics(y_test.values, y_test_pred, y_test_proba)

        # Log metrics
        for split, metrics in [("val", val_metrics), ("test", test_metrics)]:
            for metric_name, value in metrics.items():
                mlflow.log_metric(f"{split}_{metric_name}", value)

        # Create plots
        plots_dir = Path("models/artifacts/baseline")
        plots_dir.mkdir(parents=True, exist_ok=True)

        plot_pr_curve(y_test.values, y_test_proba, plots_dir / "pr_curve.png")
        plot_roc_curve(y_test.values, y_test_proba, plots_dir / "roc_curve.png")

        # Log artifacts - use fluent API which handles HTTP uploads automatically
        artifact_files = [
            ("pr_curve.png", plots_dir / "pr_curve.png"),
            ("roc_curve.png", plots_dir / "roc_curve.png"),
        ]

        for artifact_name, artifact_path in artifact_files:
            try:
                # Use fluent API - automatically uses HTTP when connected to server
                mlflow.log_artifact(str(artifact_path))
            except (OSError, Exception) as e:
                error_str = str(e)
                if "Read-only file system" in error_str or "/mlflow" in error_str:
                    print(
                        f"⚠ Warning: Could not log artifact {artifact_name} to MLflow server"
                    )
                    print(f"   Artifact saved locally at: {artifact_path}")
                    print(f"   Error: {str(e)[:100]}")
                else:
                    raise

        # Save model
        model_path = plots_dir / "model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        try:
            # Use fluent API - automatically uses HTTP when connected to server
            mlflow.log_artifact(str(model_path))
        except (OSError, Exception) as e:
            error_str = str(e)
            if "Read-only file system" in error_str or "/mlflow" in error_str:
                print("⚠ Warning: Could not log model artifact to MLflow server")
                print(f"   Model saved locally at: {model_path}")
                print(f"   Error: {str(e)[:100]}")
            else:
                raise

        print(f"Validation PR-AUC: {val_metrics['pr_auc']:.4f}")
        print(f"Test PR-AUC: {test_metrics['pr_auc']:.4f}")

        return {
            "model": model,
            "val_metrics": val_metrics,
            "test_metrics": test_metrics,
        }


def train_logistic_regression(
    train_df: pd.DataFrame, val_df: pd.DataFrame, test_df: pd.DataFrame
) -> Dict:
    """Train Logistic Regression model."""
    print("\n=== Training Logistic Regression ===")

    X_train, y_train = prepare_features(train_df)
    X_val, y_val = prepare_features(val_df)
    X_test, y_test = prepare_features(test_df)

    # Scale features for better convergence
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)

    with mlflow.start_run(run_name="logistic_regression"):
        # Log parameters
        mlflow.log_param("model_type", "logistic_regression")
        mlflow.log_param("train_samples", len(train_df))
        mlflow.log_param("val_samples", len(val_df))
        mlflow.log_param("test_samples", len(test_df))
        mlflow.log_param("features", list(X_train.columns))
        mlflow.log_param("class_weight", "balanced")
        mlflow.log_param("max_iter", 1000)
        mlflow.log_param("feature_scaling", "StandardScaler")

        # Train model
        model = LogisticRegression(
            class_weight="balanced", max_iter=1000, random_state=42
        )
        model.fit(X_train_scaled, y_train)

        # Validation metrics
        y_val_pred = model.predict(X_val_scaled)
        y_val_proba = model.predict_proba(X_val_scaled)[:, 1]
        val_metrics = compute_metrics(y_val.values, y_val_pred, y_val_proba)

        # Test metrics
        y_test_pred = model.predict(X_test_scaled)
        y_test_proba = model.predict_proba(X_test_scaled)[:, 1]
        test_metrics = compute_metrics(y_test.values, y_test_pred, y_test_proba)

        # Log metrics
        for split, metrics in [("val", val_metrics), ("test", test_metrics)]:
            for metric_name, value in metrics.items():
                mlflow.log_metric(f"{split}_{metric_name}", value)

        # Create plots
        plots_dir = Path("models/artifacts/logistic_regression")
        plots_dir.mkdir(parents=True, exist_ok=True)

        plot_pr_curve(y_test.values, y_test_proba, plots_dir / "pr_curve.png")
        plot_roc_curve(y_test.values, y_test_proba, plots_dir / "roc_curve.png")

        # Feature importance
        feature_importance = pd.DataFrame(
            {"feature": X_train.columns, "coefficient": model.coef_[0]}
        ).sort_values("coefficient", key=abs, ascending=False)

        plt.figure(figsize=(10, 6))
        plt.barh(feature_importance["feature"], feature_importance["coefficient"])
        plt.xlabel("Coefficient")
        plt.title("Feature Importance (Logistic Regression)")
        plt.tight_layout()
        plt.savefig(plots_dir / "feature_importance.png")
        plt.close()

        # Log artifacts - handle potential filesystem errors gracefully
        artifact_files = [
            ("pr_curve.png", plots_dir / "pr_curve.png"),
            ("roc_curve.png", plots_dir / "roc_curve.png"),
            ("feature_importance.png", plots_dir / "feature_importance.png"),
        ]

        for artifact_name, artifact_path in artifact_files:
            try:
                mlflow.log_artifact(str(artifact_path))
            except OSError as e:
                if "Read-only file system" in str(e) or "/mlflow" in str(e):
                    print(
                        f"⚠ Warning: Could not log artifact {artifact_name} to MLflow server"
                    )
                    print(f"   Artifact saved locally at: {artifact_path}")
                else:
                    raise

        # Save scaler with model (wrap in Pipeline for easy use)
        from sklearn.pipeline import Pipeline

        model_pipeline = Pipeline([("scaler", scaler), ("classifier", model)])

        # Log model
        try:
            mlflow.sklearn.log_model(model_pipeline, "model")
        except OSError as e:
            if "Read-only file system" in str(e) or "/mlflow" in str(e):
                print("⚠ Warning: Could not log model to MLflow server")
            else:
                raise

        # Save locally (save pipeline with scaler)
        model_path = plots_dir / "model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model_pipeline, f)

        print(f"Validation PR-AUC: {val_metrics['pr_auc']:.4f}")
        print(f"Test PR-AUC: {test_metrics['pr_auc']:.4f}")

        return {
            "model": model,
            "val_metrics": val_metrics,
            "test_metrics": test_metrics,
        }


def train_xgboost(
    train_df: pd.DataFrame, val_df: pd.DataFrame, test_df: pd.DataFrame
) -> Dict:
    """Train XGBoost model."""
    if not XGBOOST_AVAILABLE:
        print("XGBoost not available. Skipping.")
        return {}

    print("\n=== Training XGBoost ===")

    X_train, y_train = prepare_features(train_df)
    X_val, y_val = prepare_features(val_df)
    X_test, y_test = prepare_features(test_df)

    # Calculate scale_pos_weight for class imbalance
    scale_pos_weight = (y_train == 0).sum() / (y_train == 1).sum()

    with mlflow.start_run(run_name="xgboost"):
        # Log parameters
        params = {
            "model_type": "xgboost",
            "max_depth": 5,
            "learning_rate": 0.1,
            "n_estimators": 100,
            "scale_pos_weight": scale_pos_weight,
            "objective": "binary:logistic",
            "eval_metric": "aucpr",
        }

        for key, value in params.items():
            mlflow.log_param(key, value)

        mlflow.log_param("train_samples", len(train_df))
        mlflow.log_param("val_samples", len(val_df))
        mlflow.log_param("test_samples", len(test_df))
        mlflow.log_param("features", list(X_train.columns))

        # Train model
        model = xgb.XGBClassifier(
            max_depth=5,
            learning_rate=0.1,
            n_estimators=100,
            scale_pos_weight=scale_pos_weight,
            objective="binary:logistic",
            eval_metric="aucpr",
            random_state=42,
        )

        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

        # Validation metrics
        y_val_pred = model.predict(X_val)
        y_val_proba = model.predict_proba(X_val)[:, 1]
        val_metrics = compute_metrics(y_val.values, y_val_pred, y_val_proba)

        # Test metrics
        y_test_pred = model.predict(X_test)
        y_test_proba = model.predict_proba(X_test)[:, 1]
        test_metrics = compute_metrics(y_test.values, y_test_pred, y_test_proba)

        # Log metrics
        for split, metrics in [("val", val_metrics), ("test", test_metrics)]:
            for metric_name, value in metrics.items():
                mlflow.log_metric(f"{split}_{metric_name}", value)

        # Create plots
        plots_dir = Path("models/artifacts/xgboost")
        plots_dir.mkdir(parents=True, exist_ok=True)

        plot_pr_curve(y_test.values, y_test_proba, plots_dir / "pr_curve.png")
        plot_roc_curve(y_test.values, y_test_proba, plots_dir / "roc_curve.png")

        # Feature importance
        feature_importance = pd.DataFrame(
            {"feature": X_train.columns, "importance": model.feature_importances_}
        ).sort_values("importance", ascending=False)

        plt.figure(figsize=(10, 6))
        plt.barh(feature_importance["feature"], feature_importance["importance"])
        plt.xlabel("Importance")
        plt.title("Feature Importance (XGBoost)")
        plt.tight_layout()
        plt.savefig(plots_dir / "feature_importance.png")
        plt.close()

        # Log artifacts - handle potential filesystem errors gracefully
        artifact_files = [
            ("pr_curve.png", plots_dir / "pr_curve.png"),
            ("roc_curve.png", plots_dir / "roc_curve.png"),
            ("feature_importance.png", plots_dir / "feature_importance.png"),
        ]

        for artifact_name, artifact_path in artifact_files:
            try:
                mlflow.log_artifact(str(artifact_path))
            except OSError as e:
                if "Read-only file system" in str(e) or "/mlflow" in str(e):
                    print(
                        f"⚠ Warning: Could not log artifact {artifact_name} to MLflow server"
                    )
                    print(f"   Artifact saved locally at: {artifact_path}")
                else:
                    raise

        # Log model
        try:
            mlflow.xgboost.log_model(model, "model")
        except OSError as e:
            if "Read-only file system" in str(e) or "/mlflow" in str(e):
                print("⚠ Warning: Could not log model to MLflow server")
            else:
                raise

        # Save locally
        model_path = plots_dir / "model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        print(f"Validation PR-AUC: {val_metrics['pr_auc']:.4f}")
        print(f"Test PR-AUC: {test_metrics['pr_auc']:.4f}")

        return {
            "model": model,
            "val_metrics": val_metrics,
            "test_metrics": test_metrics,
        }


def train_random_forest(
    train_df: pd.DataFrame, val_df: pd.DataFrame, test_df: pd.DataFrame
) -> Dict:
    """Train Random Forest model with top 10 features."""
    print("\n=== Training Random Forest ===")

    X_train, y_train = prepare_features(train_df)
    X_val, y_val = prepare_features(val_df)
    X_test, y_test = prepare_features(test_df)

    with mlflow.start_run(run_name="random_forest"):
        # Log parameters
        params = {
            "model_type": "random_forest",
            "n_estimators": 100,
            "max_depth": 10,
            "min_samples_split": 5,
            "min_samples_leaf": 2,
            "class_weight": "balanced",
            "random_state": 42,
            "n_jobs": -1,  # Use all available cores
        }

        for key, value in params.items():
            mlflow.log_param(key, value)

        mlflow.log_param("train_samples", len(train_df))
        mlflow.log_param("val_samples", len(val_df))
        mlflow.log_param("test_samples", len(test_df))
        mlflow.log_param("features", list(X_train.columns))
        mlflow.log_param("feature_count", len(X_train.columns))

        # Train model
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )

        model.fit(X_train, y_train)

        # Get probabilities
        y_val_proba = model.predict_proba(X_val)[:, 1]
        y_test_proba = model.predict_proba(X_test)[:, 1]

        # Optimize threshold on validation set (maximize F1)
        precisions, recalls, thresholds = precision_recall_curve(
            y_val.values, y_val_proba
        )
        f1_scores = (
            2 * (precisions * recalls) / (precisions + recalls + np.finfo(float).eps)
        )
        optimal_idx = np.argmax(f1_scores)
        optimal_threshold = (
            thresholds[optimal_idx]
            if optimal_idx < len(thresholds)
            else thresholds[-1] if len(thresholds) > 0 else 0.1
        )

        # Also compute threshold matching spike rate (10% from EDA) - for reference
        target_spike_rate = 0.10
        threshold_10pct = np.percentile(y_val_proba, (1 - target_spike_rate) * 100)

        # Use optimal F1 threshold for predictions (works better across validation and test sets)
        print("\nThreshold Optimization:")
        print(f"  Optimal threshold (max F1): {optimal_threshold:.4f}")
        print(f"  Threshold for 10% spike rate: {threshold_10pct:.4f}")
        print(
            f"  Using optimal threshold (max F1): {optimal_threshold:.4f} (works across validation and test)"
        )

        # Validation metrics with optimal threshold
        y_val_pred = (y_val_proba >= optimal_threshold).astype(int)
        val_metrics = compute_metrics(y_val.values, y_val_pred, y_val_proba)

        # Test metrics with optimal threshold
        y_test_pred = (y_test_proba >= optimal_threshold).astype(int)
        test_metrics = compute_metrics(y_test.values, y_test_pred, y_test_proba)

        # Log threshold
        mlflow.log_param("optimal_threshold", optimal_threshold)
        mlflow.log_param("threshold_10pct", threshold_10pct)
        mlflow.log_param(
            "threshold_used", optimal_threshold
        )  # Indicate which threshold is used for predictions

        # Log metrics
        for split, metrics in [("val", val_metrics), ("test", test_metrics)]:
            for metric_name, value in metrics.items():
                mlflow.log_metric(f"{split}_{metric_name}", value)

        # Create plots
        plots_dir = Path("models/artifacts/random_forest")
        plots_dir.mkdir(parents=True, exist_ok=True)

        plot_pr_curve(y_test.values, y_test_proba, plots_dir / "pr_curve.png")
        plot_roc_curve(y_test.values, y_test_proba, plots_dir / "roc_curve.png")

        # Feature importance
        feature_importance = pd.DataFrame(
            {"feature": X_train.columns, "importance": model.feature_importances_}
        ).sort_values("importance", ascending=False)

        plt.figure(figsize=(10, 6))
        plt.barh(feature_importance["feature"], feature_importance["importance"])
        plt.xlabel("Importance")
        plt.title("Feature Importance (Random Forest)")
        plt.tight_layout()
        plt.savefig(plots_dir / "feature_importance.png")
        plt.close()

        # Log artifacts - handle potential filesystem errors gracefully
        artifact_files = [
            ("pr_curve.png", plots_dir / "pr_curve.png"),
            ("roc_curve.png", plots_dir / "roc_curve.png"),
            ("feature_importance.png", plots_dir / "feature_importance.png"),
        ]

        for artifact_name, artifact_path in artifact_files:
            try:
                mlflow.log_artifact(str(artifact_path))
            except OSError as e:
                if "Read-only file system" in str(e) or "/mlflow" in str(e):
                    print(
                        f"⚠ Warning: Could not log artifact {artifact_name} to MLflow server"
                    )
                    print(f"   Artifact saved locally at: {artifact_path}")
                else:
                    raise

        # Log model
        try:
            mlflow.sklearn.log_model(model, "model")
        except OSError as e:
            if "Read-only file system" in str(e) or "/mlflow" in str(e):
                print("⚠ Warning: Could not log model to MLflow server")
            else:
                raise

        # Save locally
        model_path = plots_dir / "model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        # Save threshold metadata
        threshold_metadata = {
            "optimal_threshold": float(optimal_threshold),
            "threshold_10pct": float(threshold_10pct),
            "threshold_used": float(
                optimal_threshold
            ),  # The threshold actually used for predictions (optimal F1)
            "validation_f1_at_optimal": float(f1_scores[optimal_idx]),
            "spike_rate_target": target_spike_rate,
        }
        threshold_path = plots_dir / "threshold_metadata.json"
        with open(threshold_path, "w") as f:
            json.dump(threshold_metadata, f, indent=2)

        print(f"\nSaved threshold metadata to: {threshold_path}")
        print(f"Validation PR-AUC: {val_metrics['pr_auc']:.4f}")
        print(f"Test PR-AUC: {test_metrics['pr_auc']:.4f}")
        print("\nTop 5 Features by Importance:")
        for idx, row in feature_importance.head(5).iterrows():
            print(f"  {row['feature']}: {row['importance']:.4f}")

        return {
            "model": model,
            "val_metrics": val_metrics,
            "test_metrics": test_metrics,
        }


def main():
    parser = argparse.ArgumentParser(description="Train volatility detection models")
    parser.add_argument(
        "--features",
        default="data/processed/features_labeled.parquet",
        help="Path to features parquet file",
    )
    parser.add_argument(
        "--split-method",
        choices=["time_based", "stratified"],
        default="time_based",
        help="Data splitting method: 'time_based' (maintains temporal order) or 'stratified' (balanced spike rates)",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["baseline", "logistic"],
        choices=["baseline", "logistic", "xgboost", "random_forest"],
        help="Models to train",
    )
    parser.add_argument(
        "--mlflow-uri", default="http://localhost:5001", help="MLflow tracking URI"
    )

    args = parser.parse_args()

    # Set MLflow tracking
    # If server URI is provided, try to use it; otherwise use local directory
    if args.mlflow_uri.startswith("http"):
        # Try to connect to MLflow server
        try:
            mlflow.set_tracking_uri(args.mlflow_uri)
            # Test connection with a simple health check or experiment access
            from mlflow.tracking import MlflowClient
            import urllib.request

            # Quick HTTP health check
            health_url = f"{args.mlflow_uri.rstrip('/')}/health"
            try:
                urllib.request.urlopen(health_url, timeout=2)
            except Exception:
                raise ConnectionError("MLflow server health check failed")
            # Try to access MLflow client (this will fail if server is truly not accessible)
            client = MlflowClient(tracking_uri=args.mlflow_uri)
            _ = client._tracking_client  # Access internal client to verify connection

            # Verify artifact URI is set correctly (should use server's artifact store)
            # MLflow should automatically use the server's artifact root when connected
            print(f"✓ Connected to MLflow server at {args.mlflow_uri}")

            # Ensure we're using the server's artifact store, not a local path
            # This prevents MLflow from trying to write to /mlflow locally
            import os

            # Clear any local MLFLOW_ARTIFACT_URI environment variable that might interfere
            if "MLFLOW_ARTIFACT_URI" in os.environ:
                artifact_uri = os.environ["MLFLOW_ARTIFACT_URI"]
                if artifact_uri.startswith("/mlflow"):
                    del os.environ["MLFLOW_ARTIFACT_URI"]
                    print(f"   Cleared local artifact URI: {artifact_uri}")
        except Exception as e:
            # Server not accessible, use local directory
            print(f"⚠ Warning: MLflow server at {args.mlflow_uri} not accessible")
            print(f"   Reason: {str(e)[:80]}")
            print("   Falling back to local MLflow tracking (./mlruns/)")
            # Use local directory instead
            local_mlflow_path = Path("mlruns").absolute()
            local_mlflow_path.mkdir(exist_ok=True, parents=True)
            mlflow.set_tracking_uri(f"file://{local_mlflow_path}")
            print(f"✓ Using local MLflow tracking at: {local_mlflow_path}")
    else:
        # Already a file path
        mlflow.set_tracking_uri(args.mlflow_uri)

    mlflow.set_experiment("crypto-volatility-detection")

    # Load and split data
    print("Loading data...")
    train_df, val_df, test_df = load_and_split_data(
        args.features, split_method=args.split_method
    )

    print("\nData splits:")
    print(
        f"  Train: {len(train_df)} samples ({train_df['volatility_spike'].mean():.2%} spikes)"
    )
    print(
        f"  Val:   {len(val_df)} samples ({val_df['volatility_spike'].mean():.2%} spikes)"
    )
    print(
        f"  Test:  {len(test_df)} samples ({test_df['volatility_spike'].mean():.2%} spikes)"
    )

    results = {}

    # Train models
    if "baseline" in args.models:
        results["baseline"] = train_baseline(train_df, val_df, test_df)

    if "logistic" in args.models:
        results["logistic"] = train_logistic_regression(train_df, val_df, test_df)

    if "xgboost" in args.models and XGBOOST_AVAILABLE:
        results["xgboost"] = train_xgboost(train_df, val_df, test_df)

    if "random_forest" in args.models:
        results["random_forest"] = train_random_forest(train_df, val_df, test_df)

    # Summary comparison
    print("\n=== Model Comparison (Test Set) ===")
    print(f"{'Model':<20} {'PR-AUC':<10} {'F1':<10} {'Precision':<10} {'Recall':<10}")
    print("-" * 60)

    for model_name, result in results.items():
        if result:
            m = result["test_metrics"]
            print(
                f"{model_name:<20} {m['pr_auc']:<10.4f} {m['f1_score']:<10.4f} "
                f"{m['precision']:<10.4f} {m['recall']:<10.4f}"
            )

    # Get the actual tracking URI being used
    actual_uri = mlflow.get_tracking_uri()
    print(f"\nAll models logged to MLflow: {actual_uri}")
    if actual_uri.startswith("http"):
        print(f"View results at: {actual_uri}")
    else:
        print(f"View results with: mlflow ui --backend-store-uri {actual_uri}")


if __name__ == "__main__":
    main()
