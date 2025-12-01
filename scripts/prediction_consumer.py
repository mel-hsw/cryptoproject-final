"""
Prediction Consumer: Consumes features from Kafka and automatically calls /predict API
This completes the end-to-end pipeline: Coinbase → Kafka → Features → Predictions
"""

import json
import logging
import os
import time
import argparse
import signal
import threading
from collections import deque
import requests
from kafka import KafkaConsumer
from kafka.errors import KafkaError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC_FEATURES = os.getenv("KAFKA_TOPIC_FEATURES", "ticks.features")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
PREDICT_ENDPOINT = f"{API_BASE_URL}/predict"

# Setup logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Import Kafka metrics for consumer lag tracking
try:
    from scripts.kafka_metrics import (
        start_metrics_server,
        update_consumer_lag,
        set_consumer_connected,
    )

    _HAS_KAFKA_METRICS = True
except ImportError:
    _HAS_KAFKA_METRICS = False
    logger.warning(
        "kafka_metrics module not available - consumer lag tracking disabled"
    )


class PredictionConsumer:
    """
    Consumes features from Kafka and calls /predict API automatically.

    Recommended frequency: 1-2 predictions/second (60-120/minute)
    - Matches Coinbase ticker update rate (1-2 updates/second)
    - Sufficient for 60-second prediction window
    - Optimal balance of responsiveness and resource usage

    Rate limiter set to 900/minute (15/second) as safety cap, but actual
    frequency will be limited by feature arrival rate from Kafka.
    """

    def __init__(
        self,
        api_url: str = None,
        batch_size: int = 1,
        bootstrap_servers: str = None,
        topic: str = None,
        max_requests_per_minute: int = 120,
    ):
        self.api_url = api_url or PREDICT_ENDPOINT
        self.batch_size = batch_size
        self.bootstrap_servers = bootstrap_servers or KAFKA_BOOTSTRAP
        self.topic = topic or KAFKA_TOPIC_FEATURES
        # Recommended: 120/minute (2/second) for optimal balance
        # Safety cap: 900/minute (15/second) to prevent API overload
        # Actual frequency will match feature arrival rate (typically 1-2/second)
        self.max_requests_per_minute = max_requests_per_minute
        self.consumer = None
        self.running = True
        self.shutdown_event = threading.Event()
        self.prediction_count = 0
        self.error_count = 0
        self.start_time = time.time()

        # Rate limiting: track request timestamps (keep last minute)
        # Use a deque without maxlen so we can manually prune old entries
        self.request_timestamps = deque()

        # Consumer group for metrics
        self.consumer_group = "prediction-consumer"

        # Start Prometheus metrics server for consumer lag (if enabled)
        self.metrics_port = int(os.getenv("PREDICTION_CONSUMER_METRICS_PORT", "8002"))
        if _HAS_KAFKA_METRICS:
            try:
                start_metrics_server(self.metrics_port)
            except Exception as e:
                logger.warning(f"Failed to start metrics server: {e}")

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Initialize Kafka consumer
        self._init_kafka_consumer()

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False
        self.shutdown_event.set()
        if self.consumer:
            self.consumer.close()

    def _init_kafka_consumer(self):
        """Initialize Kafka consumer with retry logic"""
        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            try:
                self.consumer = KafkaConsumer(
                    self.topic,
                    bootstrap_servers=self.bootstrap_servers,
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    auto_offset_reset="latest",  # Start from latest messages
                    group_id=self.consumer_group,
                    enable_auto_commit=True,
                    # Remove consumer_timeout_ms - let poll() handle timeouts naturally
                )
                logger.info(f"✓ Connected to Kafka at {self.bootstrap_servers}")
                logger.info(f"  Consuming from topic: {self.topic}")

                # Set consumer connected status
                if _HAS_KAFKA_METRICS:
                    set_consumer_connected(self.consumer_group, self.topic, True)

                return

            except (KafkaError, Exception) as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Kafka connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Failed to connect to Kafka after {max_retries} attempts: {e}"
                    )
                    raise

    def _extract_features_for_api(self, feature_dict: dict) -> dict:
        """Extract and format features for API request"""
        # Expected features by the model
        expected_features = [
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

        # Extract only the features needed by the model
        api_features = {}
        for feat in expected_features:
            if feat in feature_dict:
                try:
                    api_features[feat] = float(feature_dict[feat])
                except (ValueError, TypeError):
                    api_features[feat] = 0.0
            else:
                api_features[feat] = 0.0

        return api_features

    def _wait_for_rate_limit(self):
        """Wait if we're approaching rate limit"""
        now = time.time()
        # Remove timestamps older than 1 minute
        while self.request_timestamps and now - self.request_timestamps[0] > 60:
            self.request_timestamps.popleft()

        # If we're at the limit, wait until oldest request is 60 seconds old
        if len(self.request_timestamps) >= self.max_requests_per_minute:
            oldest = self.request_timestamps[0]
            wait_time = 60 - (now - oldest) + 0.1  # Add small buffer
            if wait_time > 0:
                logger.debug(
                    f"Rate limit approaching ({len(self.request_timestamps)}/{self.max_requests_per_minute}), waiting {wait_time:.2f}s"
                )
                time.sleep(wait_time)
                # Clean up again after waiting
                now = time.time()
                while self.request_timestamps and now - self.request_timestamps[0] > 60:
                    self.request_timestamps.popleft()

    def _call_predict_api(self, features: dict) -> bool:
        """Call /predict API with features"""
        # Rate limiting: wait if needed
        self._wait_for_rate_limit()

        try:
            # Format request according to API contract
            request_data = {"rows": [features]}

            # Record request timestamp
            self.request_timestamps.append(time.time())

            # Make API call
            response = requests.post(
                self.api_url,
                json=request_data,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )

            if response.status_code == 200:
                result = response.json()
                score = result.get("scores", [0])[0] if result.get("scores") else 0
                logger.debug(
                    f"Prediction successful: score={score:.4f}, "
                    f"model={result.get('model_variant', 'unknown')}"
                )
                return True
            elif response.status_code == 429:
                # Rate limit exceeded - wait longer
                logger.warning("Rate limit exceeded, waiting 5 seconds...")
                time.sleep(5)
                # Remove the timestamp since this request failed
                if self.request_timestamps:
                    self.request_timestamps.pop()
                return False
            else:
                logger.warning(
                    f"API returned status {response.status_code}: {response.text}"
                )
                # Remove the timestamp since this request failed
                if self.request_timestamps:
                    self.request_timestamps.pop()
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            # Remove the timestamp since this request failed
            if self.request_timestamps:
                self.request_timestamps.pop()
            return False
        except Exception as e:
            logger.error(f"Unexpected error calling API: {e}")
            # Remove the timestamp since this request failed
            if self.request_timestamps:
                self.request_timestamps.pop()
            return False

    def run(self):
        """Main loop: consume features and call API"""
        logger.info("Starting Prediction Consumer")
        logger.info(f"  API endpoint: {self.api_url}")
        logger.info(f"  Kafka topic: {self.topic}")
        logger.info(
            f"  Rate limit: {self.max_requests_per_minute} requests/minute ({self.max_requests_per_minute/60:.1f} requests/second)"
        )
        logger.info("  Recommended frequency: 1-2 predictions/second (60-120/minute)")
        logger.info("  Consuming features and calling /predict automatically...")
        logger.info("")

        try:
            while self.running:
                try:
                    # Poll for messages (timeout after 1 second)
                    # Returns empty dict if no messages within timeout
                    message_pack = self.consumer.poll(timeout_ms=1000)

                    if not message_pack:
                        # No messages, continue polling (this is normal)
                        continue

                    # Process messages from all partitions
                    for topic_partition, messages in message_pack.items():
                        # Update consumer lag metrics
                        if _HAS_KAFKA_METRICS and self.consumer:
                            try:
                                # Get current consumer position (committed offset)
                                committed = self.consumer.committed(topic_partition)
                                if committed is not None:
                                    # Get end offset for this partition
                                    end_offsets = self.consumer.end_offsets(
                                        [topic_partition]
                                    )
                                    end_offset = end_offsets.get(
                                        topic_partition, committed
                                    )

                                    # Update metrics
                                    update_consumer_lag(
                                        consumer_group=self.consumer_group,
                                        topic=topic_partition.topic,
                                        partition=topic_partition.partition,
                                        consumer_offset=committed,
                                        end_offset=end_offset,
                                    )
                            except Exception as e:
                                logger.debug(
                                    f"Failed to update consumer lag metrics: {e}"
                                )

                        for message in messages:
                            if not self.running:
                                break

                            try:
                                feature_dict = message.value

                                if not feature_dict:
                                    logger.warning("Received empty feature message")
                                    continue

                                # Extract features for API
                                api_features = self._extract_features_for_api(
                                    feature_dict
                                )

                                # Call /predict API
                                success = self._call_predict_api(api_features)

                                if success:
                                    self.prediction_count += 1
                                else:
                                    self.error_count += 1

                                # Log progress periodically
                                if self.prediction_count % 100 == 0:
                                    elapsed = time.time() - self.start_time
                                    rate = (
                                        self.prediction_count / elapsed
                                        if elapsed > 0
                                        else 0
                                    )
                                    logger.info(
                                        f"Processed {self.prediction_count} predictions "
                                        f"({rate:.2f} pred/s, {self.error_count} errors)"
                                    )

                            except Exception as e:
                                logger.error(f"Error processing message: {e}")
                                self.error_count += 1
                                continue

                except KafkaError as e:
                    logger.error(f"Kafka error: {e}")
                    if not self.running:
                        break
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"Unexpected error: {e}")
                    if not self.running:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.cleanup()

    def cleanup(self):
        """Cleanup resources"""
        logger.info("Cleaning up...")
        if self.consumer:
            try:
                # Set consumer disconnected status
                if _HAS_KAFKA_METRICS:
                    set_consumer_connected(self.consumer_group, self.topic, False)

                self.consumer.close()
            except Exception as e:
                logger.warning(f"Error closing consumer: {e}")

        elapsed = time.time() - self.start_time
        logger.info("=" * 50)
        logger.info("Prediction Consumer Summary:")
        logger.info(f"  Total predictions: {self.prediction_count}")
        logger.info(f"  Errors: {self.error_count}")
        logger.info(f"  Runtime: {elapsed:.1f} seconds")
        if elapsed > 0:
            logger.info(f"  Average rate: {self.prediction_count / elapsed:.2f} pred/s")
        logger.info("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="Consume features from Kafka and call /predict API automatically"
    )
    parser.add_argument(
        "--api-url",
        default=PREDICT_ENDPOINT,
        help=f"API endpoint URL (default: {PREDICT_ENDPOINT})",
    )
    parser.add_argument(
        "--topic",
        default=KAFKA_TOPIC_FEATURES,
        help=f"Kafka topic to consume from (default: {KAFKA_TOPIC_FEATURES})",
    )
    parser.add_argument(
        "--bootstrap-servers",
        default=KAFKA_BOOTSTRAP,
        help=f"Kafka bootstrap servers (default: {KAFKA_BOOTSTRAP})",
    )
    parser.add_argument(
        "--max-requests-per-minute",
        type=int,
        default=120,
        help="Maximum requests per minute (default: 120 = 2/second, recommended: 60-120/minute)",
    )

    args = parser.parse_args()

    # Create consumer with specified config
    consumer = PredictionConsumer(
        api_url=args.api_url,
        bootstrap_servers=args.bootstrap_servers,
        topic=args.topic,
        max_requests_per_minute=args.max_requests_per_minute,
    )
    consumer.run()


if __name__ == "__main__":
    main()
