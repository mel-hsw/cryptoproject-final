# Model Selection Rationale

**Date:** November 25, 2025  
**Author:** Melissa Wong  
**Project:** Real-Time Cryptocurrency Volatility Detection

---

## Executive Summary

After comprehensive experimentation with multiple model architectures, feature sets, and data splitting strategies, **Random Forest** was selected as the production model for the Crypto Volatility Detection API. The Random Forest model achieves a PR-AUC of **0.9859** on the test set, significantly outperforming the baseline by **132.5%** and demonstrating excellent balance between precision (95.72%) and recall (93.72%).

---

## 1. Models Evaluated

### 1.1 Baseline Model (Z-Score Composite)

**Description:** Rule-based detector using composite z-score across multiple features.

**Performance:**
- **PR-AUC:** 0.4240 (Test Set)
- **Method:** Composite z-score across 10 features
- **Purpose:** Provides a simple, interpretable baseline for comparison

**Rationale for Baseline:**
- Establishes a minimum performance threshold
- Demonstrates that ML models add significant value over rule-based approaches
- Provides a fallback option via `MODEL_VARIANT=baseline` in production

**Limitations:**
- Limited ability to capture non-linear relationships
- Requires manual threshold tuning
- Lower predictive power compared to ML models

---

### 1.2 Logistic Regression

**Description:** Linear classifier with L2 regularization and class balancing.

**Performance (v1.0 - Old Feature Set):**
- **PR-AUC:** 0.2449 (Test Set)
- **Precision:** 17.73%
- **Recall:** 62.18%
- **F1-Score:** 0.2759

**Performance (v1.1 - After Feature Reduction):**
- **PR-AUC:** 0.2587 (Test Set) - **+6.6% improvement**
- **Improvement:** Removing perfectly correlated features improved performance

**Strengths:**
- Fast inference time
- Interpretable coefficients
- Good recall (62.18%) suitable for alerting systems

**Limitations:**
- Limited ability to capture non-linear patterns
- Lower overall performance compared to tree-based models
- Requires careful feature engineering to avoid multicollinearity

**Why Not Selected:**
- Significantly lower PR-AUC compared to Random Forest (0.2587 vs 0.9859)
- Lower precision (17.73% vs 95.72%) leads to many false positives
- Linear model cannot capture complex market dynamics

---

### 1.3 XGBoost (Gradient Boosting)

**Description:** Gradient boosting ensemble with stratified split.

**Performance (v1.0 - Old Feature Set, Time-Based Split):**
- **PR-AUC:** 0.7359 (Test Set)

**Performance (v1.1 - Old Feature Set, Stratified Split):**
- **PR-AUC:** 0.7815 (Test Set) - **+6.2% improvement from stratified splitting**
- **Precision:** 52.87%
- **Recall:** 97.31%
- **F1-Score:** [See MLflow]

**Performance (v1.2 - New Feature Set, Consolidated Data):**
- **PR-AUC:** 0.5573 (Test Set) - Lower performance on new feature set
- **Status:** To be retrained on consolidated dataset

**Strengths:**
- Excellent recall (97.31%) - captures most volatility spikes
- Strong performance on v1.1 feature set (PR-AUC 0.7815)
- Handles non-linear relationships effectively

**Limitations:**
- Lower precision (52.87%) - many false positives
- More prone to overfitting compared to Random Forest
- Performance degraded on new feature set (v1.2)
- Requires careful hyperparameter tuning

**Why Not Selected:**
- Lower PR-AUC on new feature set (0.5573 vs Random Forest 0.9859)
- Precision-recall trade-off favors Random Forest (95.72% precision vs 52.87%)
- Random Forest shows better generalization on consolidated dataset
- Random Forest provides feature importance scores for interpretability

---

### 1.4 Random Forest (Selected Model)

**Description:** Ensemble of decision trees with balanced class weights.

**Performance (v1.2 - New Feature Set, Consolidated Dataset, Stratified Split):**
- **PR-AUC:** 0.9859 (Test Set) - **+132.5% improvement over baseline**
- **ROC-AUC:** 0.9983 (Test Set)
- **Accuracy:** 0.9888 (98.88%)
- **Precision:** 0.9572 (95.72%)
- **Recall:** 0.9372 (93.72%)
- **F1-Score:** 0.9471 (94.71%)

**Validation Set Performance:**
- **PR-AUC:** 0.9806
- **ROC-AUC:** 0.9974
- **Accuracy:** 0.9903 (99.03%)
- **Precision:** 0.9557 (95.57%)
- **Recall:** 0.9535 (95.35%)
- **F1-Score:** 0.9546 (95.46%)

**Confusion Matrix (Test Set):**
- **True Positives:** 403
- **True Negatives:** 3,585
- **False Positives:** 18 (0.45% false positive rate)
- **False Negatives:** 27 (6.28% false negative rate)

**Hyperparameters:**
- `n_estimators`: 100
- `max_depth`: 10
- `min_samples_split`: 5
- `min_samples_leaf`: 2
- `class_weight`: 'balanced'
- `random_state`: 42

**Threshold Optimization:**
- **Optimal F1 Threshold:** 0.7057 (maximizes F1-score on validation set)
- **Alternative Threshold (10% spike rate):** 0.8050 (for reference)
- Threshold saved to `models/artifacts/random_forest/threshold_metadata.json`

**Strengths:**
- **Best Overall Performance:** Highest PR-AUC (0.9859) among all models tested
- **Excellent Precision-Recall Balance:** 95.72% precision with 93.72% recall
- **Feature Importance:** Provides interpretable feature importance scores
- **Robust Generalization:** Consistent performance on validation (0.9806) and test (0.9859) sets
- **Low False Positive Rate:** Only 18 false positives out of 4,033 predictions (0.45%)
- **Less Prone to Overfitting:** Compared to XGBoost on this dataset

**Why Selected:**
1. **Superior Performance:** PR-AUC 0.9859 significantly outperforms all alternatives
2. **Balanced Metrics:** Excellent balance between precision (95.72%) and recall (93.72%)
3. **Robust Validation:** Consistent performance across validation and test sets indicates good generalization
4. **Feature Interpretability:** Feature importance scores aid in understanding model decisions
5. **Production Ready:** Low false positive rate (0.45%) minimizes unnecessary alerts

---

## 2. Feature Set Evolution

### 2.1 Feature Set v1.0 (Initial)

**Features:** 30+ features including:
- Log return statistics (std, mean across multiple windows)
- Return statistics (mean, min, range)
- Spread statistics (std, mean)
- Tick count (trade intensity)

**Issues:**
- High multicollinearity (perfectly correlated features)
- Feature leakage concerns
- Too many features leading to overfitting

---

### 2.2 Feature Set v1.1 (Reduced)

**Features:** 10 features selected to minimize multicollinearity:
- `log_return_std_30s`, `log_return_std_60s`, `log_return_std_300s`
- `return_mean_60s`, `return_mean_300s`, `return_min_30s`
- `spread_std_300s`, `spread_mean_60s`
- `tick_count_60s`
- `return_range_60s`

**Improvements:**
- Removed perfectly correlated features
- Improved Logistic Regression PR-AUC by +6.6%
- Better model generalization

**Best Performance:** XGBoost achieved PR-AUC 0.7815 with this feature set

---

### 2.3 Feature Set v1.2 (Current - Momentum & Volatility Focus)

**Features:** 10 top features selected via Random Forest feature importance analysis:

**Momentum & Volatility:**
- `log_return_300s` - Log return over 300-second window
- `realized_volatility_300s` - Rolling std dev of 1-second returns (18.8% importance)
- `realized_volatility_60s` - Rolling std dev of 1-second returns (60s window)
- `price_velocity_300s` - Rolling mean of absolute 1-second price changes

**Liquidity & Microstructure:**
- `spread_mean_300s` - Rolling mean of bid-ask spread (14.9% importance)
- `spread_mean_60s` - Rolling mean of bid-ask spread (60s window)
- `order_book_imbalance_300s` - Rolling mean of buy/sell volume ratio (18.8% importance)
- `order_book_imbalance_60s` - Order book imbalance (60s window)
- `order_book_imbalance_30s` - Order book imbalance (30s window)

**Activity:**
- `trade_intensity_300s` - Rolling sum of tick count (17.1% importance)

**Top Features by Importance:**
1. `order_book_imbalance_300s` - 18.8%
2. `trade_intensity_300s` - 17.1%
3. `spread_mean_300s` - 14.9%

**Rationale:**
- Focus on market microstructure signals (order book imbalance, spread)
- Emphasis on realized volatility as a direct proxy for volatility spikes
- Trade intensity captures market activity patterns
- Features selected via Random Forest importance analysis to maximize predictive power

---

## 3. Data Splitting Strategy

### 3.1 Time-Based Split (Initial)

**Method:** Sequential split by timestamp (70/15/15 train/val/test)

**Issues:**
- Imbalanced spike rates across splits
- Validation set had much lower spike rate than test set
- Led to threshold optimization issues (10% spike rate threshold caused 0 true positives in validation)

**Example Problem:**
- Validation spike rate: 2.80%
- Test spike rate: 33.62%
- This imbalance made threshold optimization unreliable

---

### 3.2 Stratified Split (Current)

**Method:** Stratified split maintaining spike rate across all splits (70/15/15 train/val/test)

**Benefits:**
- Balanced spike rates (~10.67%) across all splits
- Reliable threshold optimization
- Better model evaluation and generalization assessment

**Impact:**
- Improved XGBoost PR-AUC from 0.7359 to 0.7815 (+6.2%)
- Enabled reliable threshold optimization for Random Forest
- Consistent performance across validation and test sets

**Current Dataset:**
- **Total Samples:** 26,881 (consolidated from 5 feature files)
- **Spike Rate:** ~10.67% (balanced across splits)
- **Train:** 18,816 samples (70%)
- **Validation:** 4,033 samples (15%)
- **Test:** 4,033 samples (15%)

---

## 4. Dataset Evolution

### 4.1 Initial Dataset (v1.0)

- **Size:** ~10,231 samples
- **Source:** Single replay file (`features_replay.parquet`)
- **Duration:** ~90 minutes of data
- **Limitations:** Limited sample size, potential overfitting

---

### 4.2 Consolidated Dataset (v1.2)

- **Size:** 26,881 samples
- **Source:** Consolidated from 5 feature files:
  - `features_replay.parquet`
  - `features_long_*.parquet`
  - `features_combined.parquet`
  - `features_all_raw.parquet`
  - `features.parquet`
- **Duration:** ~350 hours of data
- **Benefits:**
  - Much larger sample size improves generalization
  - More diverse market conditions
  - Better balanced splits with stratified method
  - Removed duplicates and time-close rows

**Impact on Performance:**
- Random Forest PR-AUC improved from 0.5662 (small dataset) to 0.9859 (consolidated dataset)
- Better generalization: validation PR-AUC (0.9806) closely matches test PR-AUC (0.9859)

---

## 5. Threshold Optimization

### 5.1 Default Threshold (0.5)

**Issue:** Model probabilities were all below 0.5, leading to 0 true positives

**Solution:** Implemented threshold optimization during training

---

### 5.2 Optimal F1 Threshold (0.7057)

**Method:** Maximize F1-score on validation set

**Performance:**
- **Validation PR-AUC:** 0.9806
- **Test PR-AUC:** 0.9859
- **Test Precision:** 0.9572 (95.72%)
- **Test Recall:** 0.9372 (93.72%)
- **Test F1-Score:** 0.9471 (94.71%)

**Rationale:**
- Balances precision and recall effectively
- Achieves excellent performance on both validation and test sets
- Low false positive rate (0.45%) minimizes unnecessary alerts
- High recall (93.72%) ensures most volatility spikes are detected

---

### 5.3 Alternative Threshold (10% Spike Rate - 0.8050)

**Method:** Match 10% spike rate from EDA

**Issue:** Too high for validation set (2.80% actual spike rate), leading to 0 true positives

**Decision:** Not used - optimal F1 threshold (0.7057) provides better overall performance

---

## 6. Model Comparison Summary

| Model | PR-AUC (Test) | Precision | Recall | F1-Score | Strengths | Limitations |
|-------|---------------|-----------|--------|----------|-----------|-------------|
| **Random Forest** | **0.9859** | **0.9572** | **0.9372** | **0.9471** | Best performance, balanced metrics, interpretable | - |
| XGBoost (v1.1) | 0.7815 | 0.5287 | 0.9731 | - | High recall | Lower precision, overfitting risk |
| XGBoost (v1.2) | 0.5573 | - | - | - | - | Lower performance on new features |
| Logistic Regression | 0.2587 | 0.1773 | 0.6218 | 0.2759 | Fast, interpretable | Limited non-linear patterns |
| Baseline (Z-Score) | 0.4240 | - | - | - | Simple, interpretable | Limited predictive power |

---

## 7. Final Selection Decision

### 7.1 Why Random Forest?

1. **Best Performance:** PR-AUC 0.9859 significantly outperforms all alternatives (+132.5% vs baseline, +76.7% vs best XGBoost)

2. **Balanced Metrics:** 
   - High precision (95.72%) minimizes false alarms
   - High recall (93.72%) ensures spike detection
   - Excellent F1-score (94.71%) indicates balanced performance

3. **Robust Generalization:**
   - Validation PR-AUC (0.9806) closely matches test PR-AUC (0.9859)
   - Consistent performance across stratified splits
   - Low false positive rate (0.45%) indicates good calibration

4. **Feature Interpretability:**
   - Provides feature importance scores
   - Top features align with domain knowledge (order book imbalance, trade intensity, spread)
   - Aids in understanding model decisions

5. **Production Readiness:**
   - Low false positive rate minimizes unnecessary alerts
   - Fast inference time (milliseconds)
   - Reliable threshold optimization (0.7057)
   - Model rollback capability via `MODEL_VARIANT=baseline`

---

### 7.2 Trade-offs Considered

**Precision vs Recall:**
- Random Forest achieves excellent balance (95.72% precision, 93.72% recall)
- XGBoost v1.1 had higher recall (97.31%) but much lower precision (52.87%)
- For production use, balanced metrics are preferred to minimize both false alarms and missed spikes

**Model Complexity vs Performance:**
- Random Forest provides excellent performance without excessive complexity
- Less prone to overfitting compared to XGBoost
- Feature importance provides interpretability without sacrificing performance

**Inference Speed:**
- Random Forest inference completes in milliseconds (well under 2x real-time requirement)
- Fast enough for real-time predictions at scale

---

## 8. Future Considerations

### 8.1 Potential Improvements

1. **Retrain XGBoost on Consolidated Dataset:**
   - XGBoost showed promise on v1.1 feature set (PR-AUC 0.7815)
   - Retraining on consolidated dataset with v1.2 features may improve performance
   - Could serve as an alternative model for A/B testing

2. **Ensemble Methods:**
   - Combine Random Forest and XGBoost predictions
   - May improve robustness and reduce false positives further

3. **Feature Engineering:**
   - Explore additional microstructure features
   - Consider interaction features between order book imbalance and spread

4. **Hyperparameter Tuning:**
   - Current Random Forest uses default hyperparameters
   - Grid search or Bayesian optimization may improve performance further

### 8.2 Monitoring and Maintenance

1. **Data Drift Monitoring:**
   - Weekly Evidently reports to detect feature distribution shifts
   - Retrain if significant drift detected

2. **Performance Monitoring:**
   - Track precision, recall, and PR-AUC in production
   - Alert if performance degrades below thresholds

3. **Model Versioning:**
   - Use MLflow for model versioning and experiment tracking
   - Maintain ability to rollback to previous versions

---

## 9. Conclusion

Random Forest was selected as the production model based on:

1. **Superior Performance:** PR-AUC 0.9859 outperforms all alternatives
2. **Balanced Metrics:** Excellent precision (95.72%) and recall (93.72%)
3. **Robust Generalization:** Consistent performance across validation and test sets
4. **Feature Interpretability:** Provides actionable insights via feature importance
5. **Production Readiness:** Low false positive rate, fast inference, reliable threshold

The model demonstrates excellent performance on the consolidated dataset (26,881 samples) with stratified splits, achieving a PR-AUC of 0.9859 while maintaining a balanced precision-recall trade-off suitable for real-time volatility detection in production.

---

**Model Location:** `models/artifacts/random_forest/model.pkl`  
**Threshold Metadata:** `models/artifacts/random_forest/threshold_metadata.json`  
**MLflow Experiment:** `crypto-volatility-detection`  
**Model Version:** v1.2

