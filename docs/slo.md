# Service Level Objectives (SLOs)

## Overview

This document defines the Service Level Objectives (SLOs) for the Crypto Volatility Detection API. SLOs are aspirational targets that guide system reliability and performance goals.

## SLO Definitions

### 1. Latency SLO

**Target:** p95 latency ≤ 800ms (aspirational)

- **Measurement:** 95th percentile of prediction latency measured over 5-minute windows
- **Metric:** `histogram_quantile(0.95, rate(prediction_latency_seconds_bucket[5m]))`
- **Current Performance:** Tracked in Grafana dashboard
- **Alert Threshold:** p95 > 1000ms for 5 consecutive minutes

**Rationale:**
- Real-time volatility detection requires low latency
- 800ms allows for feature computation and model inference
- Aspirational target - actual performance may vary based on load

### 2. Availability SLO

**Target:** 99.5% uptime

- **Measurement:** Percentage of successful health checks over 1-hour windows
- **Metric:** `sum(rate(http_requests_total{endpoint="/health",status="200"}[1h])) / sum(rate(http_requests_total{endpoint="/health"}[1h]))`
- **Alert Threshold:** Availability < 99% for 15 consecutive minutes

**Rationale:**
- High availability ensures continuous monitoring
- 99.5% allows for planned maintenance and unexpected downtime
- Equivalent to ~3.6 hours downtime per month

### 3. Error Rate SLO

**Target:** Error rate < 1%

- **Measurement:** Percentage of 4xx/5xx responses over total requests
- **Metric:** `sum(rate(http_requests_total{status=~"4..|5.."}[5m])) / sum(rate(http_requests_total[5m]))`
- **Alert Threshold:** Error rate > 2% for 5 consecutive minutes

**Rationale:**
- Low error rate ensures reliable predictions
- 1% allows for edge cases and data quality issues
- Critical for production reliability

### 4. Success Rate SLO

**Target:** Success rate ≥ 99%

- **Measurement:** Percentage of 2xx responses over total requests
- **Metric:** `sum(rate(http_requests_total{status=~"2.."}[5m])) / sum(rate(http_requests_total[5m]))`
- **Alert Threshold:** Success rate < 98% for 5 consecutive minutes

**Rationale:**
- Ensures API is functioning correctly
- 99% success rate indicates healthy system operation
- Complements error rate SLO

## Monitoring

All SLOs are monitored via:
- **Grafana Dashboard:** `http://localhost:3000` (see `crypto-volatility` dashboard)
- **Prometheus Metrics:** `http://localhost:9090`
- **Evidently Reports:** `reports/evidently/` (for data quality)

## Review Schedule

SLOs are reviewed monthly and adjusted based on:
- Actual performance trends
- Business requirements
- Infrastructure capacity
- User feedback

## SLO Violation Response

When SLOs are violated:
1. **Immediate:** Check Grafana dashboard for root cause
2. **Short-term:** Review logs and metrics
3. **Long-term:** Update runbook with mitigation steps
4. **Escalation:** If persistent, consider model rollback or infrastructure scaling

---

**Last Updated:** November 2025  
**Next Review:** December 2025

