---
title: "Load Test Latency Report"
author: "Crypto Project Team"
date: "Week 5"
output: html_document
---

# Load Test Latency Report

## Executive Summary

This report analyzes the latency performance of our cryptocurrency prediction API based on load testing conducted with 100 concurrent requests. The system demonstrated excellent reliability with a 100% success rate and strong latency characteristics.

## Test Configuration

- **Total Requests**: 100
- **Test Duration**: 0.82 seconds
- **Concurrent Execution**: Yes
- **Test Type**: Load test

## Performance Metrics

### Success Rate
- **Successful Requests**: 100/100
- **Failed Requests**: 0
- **Success Rate**: 100.0%

### Throughput
- **Requests per Second**: 121.92 RPS

### Latency Distribution

| Metric | Value (ms) |
|--------|------------|
| Minimum | 12.00 |
| Maximum | 94.17 |
| Mean | 64.24 |
| Median (P50) | 67.48 |
| P95 | 91.17 |
| P99 | 94.16 |

## Analysis

### Latency Characteristics

1. **Central Tendency**
   - The mean latency of 64.24ms indicates generally fast response times
   - The median (67.48ms) is slightly higher than the mean, suggesting a slight left skew in the distribution
   - Most requests complete within 60-70ms

2. **Tail Latencies**
   - P95 latency of 91.17ms means 95% of requests complete in under 91ms
   - P99 latency of 94.16ms shows even the slowest 1% of requests complete within reasonable time
   - The gap between median and P99 (26.68ms) is relatively small, indicating consistent performance

3. **Outliers**
   - The fastest request completed in 12.00ms (request_id: 73)
   - The slowest request took 94.17ms (request_id: 64)
   - Range of 82.17ms shows some variance but all requests remain under 100ms

### Performance Patterns

Looking at the individual request results, we observe several performance clusters:

- **Fast responses (10-50ms)**: Requests 73, 74, 76-79, 81 showed exceptional performance
- **Typical responses (50-80ms)**: The majority of requests fell in this range
- **Slower responses (80-95ms)**: Requests 63-72 showed the highest latencies

This clustering suggests the system may have different code paths or is affected by varying input complexity.

## Conclusions

### Strengths
1. **100% Success Rate**: No failures or errors during the load test
2. **Consistent Performance**: Small gap between P50 and P99 latencies
3. **Sub-100ms Response Times**: All requests completed within 100ms
4. **High Throughput**: Sustained 121+ requests per second

### Recommendations

1. **Performance Target**: Consider setting a P95 latency SLO of <90ms based on current performance
2. **Investigation**: Analyze the performance difference between fast (12ms) and typical (67ms) requests to understand if optimizations are possible
3. **Monitoring**: Implement real-time latency monitoring to detect degradation
4. **Load Testing**: Conduct additional tests with higher concurrency (500, 1000 requests) to identify breaking points

## Appendix: Test Methodology

The load test was executed by sending 100 concurrent requests to the prediction API endpoint. Each request was tracked for:
- Response status code
- Latency in milliseconds
- Success/failure status
- Error messages (if any)

All 100 requests returned HTTP 200 status codes with no errors reported.
