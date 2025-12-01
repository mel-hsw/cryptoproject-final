"""
Shared Kafka consumer metrics module for Prometheus.
Exposes consumer lag and other Kafka-related metrics.
"""

import logging
from prometheus_client import Gauge, start_http_server
from typing import Optional

logger = logging.getLogger(__name__)

# Consumer lag metrics
kafka_consumer_lag = Gauge(
    "kafka_consumer_lag",
    "Kafka consumer lag (number of messages behind)",
    ["consumer_group", "topic", "partition"],
)

kafka_consumer_offset = Gauge(
    "kafka_consumer_offset",
    "Current Kafka consumer offset",
    ["consumer_group", "topic", "partition"],
)

kafka_topic_end_offset = Gauge(
    "kafka_topic_end_offset",
    "End offset of Kafka topic partition",
    ["topic", "partition"],
)

# Consumer health metrics
kafka_consumer_connected = Gauge(
    "kafka_consumer_connected",
    "Whether Kafka consumer is connected (1) or not (0)",
    ["consumer_group", "topic"],
)

# Start metrics server (call this from consumer scripts)
def start_metrics_server(port: int = 8001):
    """Start Prometheus metrics HTTP server."""
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
    except OSError as e:
        logger.warning(f"Failed to start metrics server on port {port}: {e}")
        logger.info("Metrics server may already be running or port may be in use")


def update_consumer_lag(
    consumer_group: str,
    topic: str,
    partition: int,
    consumer_offset: int,
    end_offset: int,
):
    """Update consumer lag metrics."""
    lag = max(0, end_offset - consumer_offset)
    
    kafka_consumer_lag.labels(
        consumer_group=consumer_group,
        topic=topic,
        partition=partition,
    ).set(lag)
    
    kafka_consumer_offset.labels(
        consumer_group=consumer_group,
        topic=topic,
        partition=partition,
    ).set(consumer_offset)
    
    kafka_topic_end_offset.labels(
        topic=topic,
        partition=partition,
    ).set(end_offset)


def set_consumer_connected(consumer_group: str, topic: str, connected: bool):
    """Set consumer connection status."""
    kafka_consumer_connected.labels(
        consumer_group=consumer_group,
        topic=topic,
    ).set(1 if connected else 0)

