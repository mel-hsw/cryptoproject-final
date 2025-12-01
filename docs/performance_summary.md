# Performance Summary

## Overview

This document summarizes the performance metrics, uptime statistics, and model performance comparisons for the Crypto Volatility Detection API.

**Report Period:** November 2025  
**Last Updated:** November 25, 2025

---

## Latency Metrics

### Load Test Results

**Test Date:** November 25, 2025  
**Test Configuration:**
- Total Requests: 100 (burst)
- Concurrent Requests: 10
- Duration: 0.82 seconds

**Results:**
```json
{
  "total_requests": 100,
  "successful_requests": 100,
  "failed_requests": 0,
  "success_rate": 100.0,
  "requests_per_second": 121.92,
  "latency_ms": {
    "min": 12.00,
    "max": 94.17,
    "mean": 64.24,
    "median": 67.48,
    "p50": 67.48,
    "p95": 91.17,
    "p99": 94.16
  }
}
```

**Key Findings:**
- ✅ **100% success rate** - All requests completed successfully
- ✅ **p95 latency: 91.17ms** - Well below 800ms target (88.6% better than target!)
- ✅ **121.92 requests/second** - Excellent throughput for real-time predictions
- ✅ **No errors** - System handled burst load gracefully
- ✅ **p50 latency: 67.48ms** - Median response time is very fast

**Run Load Test:**
```bash
python scripts/load_test.py --requests 100 --output docs/load_test_results.json
```

---

## Model Performance Comparison

### Random Forest vs Baseline

| Metric | Random Forest (Test) | Baseline (Test) | Improvement |
|--------|---------------------|----------------|-------------|
| PR-AUC | **0.9859** | 0.1039 | **+849%** (9.5x better) |
| ROC-AUC | **0.9983** | 0.4228 | **+136%** (2.4x better) |
| Accuracy | **0.9888** | 0.8854 | **+11.7%** |
| Precision | **0.9572** | 0.2714 | **+253%** (3.5x better) |
| Recall | **0.9372** | 0.0442 | **+2,020%** (21.2x better) |
| F1-Score | **0.9471** | 0.0760 | **+1,146%** (12.5x better) |

**Note:** Model performance metrics are logged to MLflow. Check `http://localhost:5001` for detailed metrics.

### Model Evaluation

**Random Forest Model:**
- **Location:** `models/artifacts/random_forest/`
- **Training Date:** November 24, 2025
- **Features:** 10 top features from feature analysis
- **Dataset:** Consolidated dataset (26,881 samples, stratified splits)
- **Split Method:** Stratified (70/15/15 train/val/test)

**Test Set Performance:**
- **PR-AUC:** 0.9859
- **ROC-AUC:** 0.9983
- **Accuracy:** 0.9888 (98.88%)
- **Precision:** 0.9572 (95.72%)
- **Recall:** 0.9372 (93.72%)
- **F1-Score:** 0.9471 (94.71%)
- **Confusion Matrix:**
  - True Positives: 403
  - True Negatives: 3,585
  - False Positives: 18
  - False Negatives: 27

**Validation Set Performance:**
- **PR-AUC:** 0.9806
- **ROC-AUC:** 0.9974
- **Accuracy:** 0.9903 (99.03%)
- **Precision:** 0.9557 (95.57%)
- **Recall:** 0.9535 (95.35%)
- **F1-Score:** 0.9546 (95.46%)
- **Confusion Matrix:**
  - True Positives: 410
  - True Negatives: 3,583
  - False Positives: 19
  - False Negatives: 20

**Top Features:** order_book_imbalance_300s (18.8%), trade_intensity_300s (17.1%), spread_mean_300s (14.9%)

**Baseline Model:**
- **Location:** `models/artifacts/baseline/`
- **Method:** Z-score based volatility detection
- **Training Date:** November 25, 2025
- **Dataset:** Consolidated dataset (26,881 samples, stratified splits)
- **Split Method:** Stratified (70/15/15 train/val/test)

**Test Set Performance:**
- **PR-AUC:** 0.1039
- **ROC-AUC:** 0.4228
- **Accuracy:** 0.8854 (88.54%)
- **Precision:** 0.2714 (27.14%)
- **Recall:** 0.0442 (4.42%) - **Very low recall, missing 95.58% of spikes**
- **F1-Score:** 0.0760 (7.60%)
- **Confusion Matrix:**
  - True Positives: 19
  - True Negatives: 3,552
  - False Positives: 51
  - False Negatives: 411

**Validation Set Performance:**
- **PR-AUC:** 0.0861
- **ROC-AUC:** 0.3854
- **Accuracy:** 0.8797 (87.97%)
- **Precision:** 0.1333 (13.33%)
- **Recall:** 0.0233 (2.33%) - **Extremely low recall, missing 97.67% of spikes**
- **F1-Score:** 0.0396 (3.96%)
- **Confusion Matrix:**
  - True Positives: 10
  - True Negatives: 3,537
  - False Positives: 65
  - False Negatives: 420

**Model Comparison:**
- Random Forest significantly outperforms Baseline across all metrics
- **PR-AUC:** Random Forest 0.9859 vs Baseline 0.1039 (**+849% improvement, 9.5x better**)
- **Recall:** Random Forest captures 93.72% of spikes vs Baseline's 4.42% (**21.2x better recall**)
- **Precision:** Random Forest 95.72% vs Baseline 27.14% (**3.5x better precision**)
- Baseline model has very low recall (4.42%), missing 95.58% of volatility spikes, making it unsuitable for production use
- Both models trained on consolidated dataset with stratified splits for fair comparison

**Generate Evaluation Report:**
```bash
python scripts/generate_eval_report.py \
  --features data/processed/features_consolidated.parquet \
  --model models/artifacts/random_forest/model.pkl \
  --output reports/model_eval.pdf
```

**Key Findings:**
- ✅ **Excellent PR-AUC (0.9859)** - Model demonstrates strong ability to distinguish volatility spikes
- ✅ **High Precision (0.9572)** - Low false positive rate (18 out of 4,033 predictions)
- ✅ **High Recall (0.9372)** - Captures 93.72% of actual volatility spikes
- ✅ **Balanced Performance** - F1-score of 0.9471 indicates good balance between precision and recall
- ✅ **Consistent Validation Performance** - Validation metrics (PR-AUC 0.9806) align closely with test metrics, indicating good generalization
- ✅ **Low Error Rate** - Only 45 total errors (18 FP + 27 FN) out of 4,033 test predictions (1.12% error rate)
- ✅ **Massive Improvement over Baseline** - Random Forest is 9.5x better in PR-AUC and 21.2x better in recall compared to baseline
- ⚠️ **Baseline Limitations** - Baseline model has extremely low recall (4.42%), missing 95.58% of volatility spikes, making it unsuitable for production use

---

## SLO Compliance

| SLO | Target | Current | Status | Notes |
|-----|--------|---------|--------|-------|
| p95 Latency ≤ 800ms | ≤ 800ms | 91.17ms | ✅ PASS | 88.6% better than target |
| Availability ≥ 99.5% | ≥ 99.5% | 100% (load test) | ✅ PASS | Based on load test results |
| Error Rate < 1% | < 1% | 0% | ✅ PASS | No errors in load test (100 requests) |
| Success Rate ≥ 99% | ≥ 99% | 100% | ✅ PASS | All requests successful in load test |
| Throughput | N/A | 121.92 req/s | ✅ GOOD | Handles burst traffic well |

**Note:** SLO compliance metrics are based on load test results (100 requests). Production monitoring would require extended deployment period.

---

## Data Collection

### Collect Metrics

```bash
# 1. Run load test
python scripts/load_test.py --requests 100 --output docs/load_test_results.json

# 2. Query Prometheus for uptime
curl 'http://localhost:9090/api/v1/query?query=sum(rate(http_requests_total{endpoint="/health",status="200"}[24h]))/sum(rate(http_requests_total{endpoint="/health"}[24h]))*100'

# 3. Query Prometheus for latency
curl 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,rate(prediction_latency_seconds_bucket[5m]))*1000'

# 4. Generate model evaluation
python scripts/generate_eval_report.py \
  --features data/processed/features_consolidated.parquet \
  --model models/artifacts/random_forest/model.pkl
```

---

**Next Update:** As needed based on model retraining or new data collection  
**Report Owner:** Melissa Wong

