"""
Feature engineering pipeline: Kafka consumer that computes windowed features
from raw tick data and publishes to ticks.features topic.
"""

import argparse
import json
import logging
import signal
import threading
import time
import os
from collections import deque
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np
from pathlib import Path

# Setup logging first
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Import Kafka metrics for consumer lag tracking
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.kafka_metrics import (
        start_metrics_server,
        update_consumer_lag,
        set_consumer_connected,
    )
    _HAS_KAFKA_METRICS = True
except ImportError:
    _HAS_KAFKA_METRICS = False
    logger.warning("kafka_metrics module not available - consumer lag tracking disabled")


# Lazy import for Kafka (only needed for FeaturePipeline, not FeatureComputer)
def _import_kafka():
    """Lazy import Kafka classes only when needed."""
    from kafka import KafkaConsumer, KafkaProducer
    from kafka.errors import KafkaError, KafkaTimeoutError

    return KafkaConsumer, KafkaProducer, KafkaError, KafkaTimeoutError


# Logging is already configured above


class FeatureComputer:
    """Computes windowed features from streaming tick data."""

    def __init__(
        self,
        window_sizes: list = [30, 60, 300],  # seconds
        max_buffer_size: int = 10000,
    ):
        """
        Initialize feature computer with sliding windows.

        Args:
            window_sizes: List of window sizes in seconds
            max_buffer_size: Maximum number of ticks to keep in memory
        """
        self.window_sizes = window_sizes
        self.max_buffer_size = max_buffer_size

        # Buffers for different data types
        self.ticks_buffer = deque(maxlen=max_buffer_size)
        self.prices_buffer = deque(maxlen=max_buffer_size)
        self.timestamps_buffer = deque(maxlen=max_buffer_size)
        self.spreads_buffer = deque(maxlen=max_buffer_size)  # For spread volatility

        # Note: 1-second returns are computed on-the-fly in compute_features to avoid buffer alignment issues

        # Buffers for order book data (for Order Book Imbalance)
        self.bid_quantities_buffer = deque(maxlen=max_buffer_size)
        self.ask_quantities_buffer = deque(maxlen=max_buffer_size)

        # Buffers for trade sizes (for Volume Velocity)
        self.trade_sizes_buffer = deque(maxlen=max_buffer_size)

        # Track last tick time for time-since-last-trade feature
        self.last_tick_time = None

        logger.info(f"FeatureComputer initialized with windows: {window_sizes}s")

    def _get_midprice(self, tick: Dict[str, Any]) -> Optional[float]:
        """Extract midprice from tick data."""
        try:
            # Try direct price field first
            if "price" in tick:
                return float(tick["price"])

            # Calculate from bid/ask
            best_bid = float(tick.get("best_bid", 0))
            best_ask = float(tick.get("best_ask", 0))

            if best_bid > 0 and best_ask > 0:
                return (best_bid + best_ask) / 2.0
            return None
        except (ValueError, TypeError):
            return None

    def _get_spread(self, tick: Dict[str, Any]) -> Optional[float]:
        """Calculate bid-ask spread."""
        try:
            best_bid = float(tick.get("best_bid", 0))
            best_ask = float(tick.get("best_ask", 0))

            if best_bid > 0 and best_ask > 0:
                return best_ask - best_bid
            return None
        except (ValueError, TypeError):
            return None

    def _get_spread_bps(self, tick: Dict[str, Any]) -> Optional[float]:
        """Calculate bid-ask spread in basis points."""
        try:
            best_bid = float(tick.get("best_bid", 0))
            best_ask = float(tick.get("best_ask", 0))
            midprice = (best_bid + best_ask) / 2.0

            if midprice > 0:
                spread = best_ask - best_bid
                return (spread / midprice) * 10000  # basis points
            return None
        except (ValueError, TypeError):
            return None

    def _get_order_book_quantities(self, tick: Dict[str, Any]) -> tuple:
        """Extract bid and ask quantities from tick data."""
        try:
            # Try to get from raw field first (Coinbase Advanced Trade format)
            raw = tick.get("raw", {})
            bid_qty = raw.get("best_bid_quantity") or tick.get("best_bid_quantity")
            ask_qty = raw.get("best_ask_quantity") or tick.get("best_ask_quantity")

            if bid_qty is not None and ask_qty is not None:
                return float(bid_qty), float(ask_qty)
            return None, None
        except (ValueError, TypeError):
            return None, None

    def _get_trade_size(self, tick: Dict[str, Any]) -> Optional[float]:
        """Extract trade size from tick data."""
        try:
            # Try multiple possible field names
            size = tick.get("size") or tick.get("volume") or tick.get("trade_size")
            if size is not None:
                return float(size)
            return None
        except (ValueError, TypeError):
            return None

    def add_tick(self, tick: Dict[str, Any]):
        """Add a new tick to the buffer."""
        # Parse timestamp first to validate ordering
        timestamp_str = tick.get("timestamp", tick.get("time"))
        if timestamp_str:
            try:
                ts = pd.to_datetime(timestamp_str, utc=True)
            except Exception:
                # Fallback to current UTC time
                ts = pd.Timestamp.now(tz="UTC")
        else:
            ts = pd.Timestamp.now(tz="UTC")

        # Validate timestamp ordering before adding
        if not self._validate_timestamp_order(ts):
            # Still add the tick but log the issue
            logger.warning(
                "Adding tick with out-of-order timestamp (may indicate data quality issue)"
            )

        # Add tick to buffers
        self.ticks_buffer.append(tick)

        # Store timestamp first (needed for 1-second return calculation)
        self.timestamps_buffer.append(ts)
        self.last_tick_time = ts

        # Extract and store price
        midprice = self._get_midprice(tick)
        if midprice:
            self.prices_buffer.append(midprice)

        # Store spread for spread volatility computation
        spread = self._get_spread(tick)
        if spread is not None:
            self.spreads_buffer.append(spread)

        # Store order book quantities for Order Book Imbalance
        bid_qty, ask_qty = self._get_order_book_quantities(tick)
        if bid_qty is not None:
            self.bid_quantities_buffer.append(bid_qty)
        if ask_qty is not None:
            self.ask_quantities_buffer.append(ask_qty)

        # Store trade size for Volume Velocity
        trade_size = self._get_trade_size(tick)
        if trade_size is not None:
            self.trade_sizes_buffer.append(trade_size)

    def _get_window_data(self, window_seconds: int) -> tuple:
        """
        Get data within the specified time window.

        Returns:
            (prices_in_window, timestamps_in_window, ticks_in_window, spreads_in_window,
             bid_quantities_in_window, ask_quantities_in_window, trade_sizes_in_window)
        """
        if len(self.timestamps_buffer) < 2:
            return [], [], [], [], [], [], []

        current_time = self.timestamps_buffer[-1]
        cutoff_time = current_time - pd.Timedelta(seconds=window_seconds)

        prices_in_window = []
        timestamps_in_window = []
        ticks_in_window = []
        spreads_in_window = []
        bid_quantities_in_window = []
        ask_quantities_in_window = []
        trade_sizes_in_window = []

        for i, ts in enumerate(self.timestamps_buffer):
            if ts >= cutoff_time:
                timestamps_in_window.append(ts)
                if i < len(self.prices_buffer):
                    prices_in_window.append(self.prices_buffer[i])
                if i < len(self.ticks_buffer):
                    ticks_in_window.append(self.ticks_buffer[i])
                if i < len(self.spreads_buffer):
                    spreads_in_window.append(self.spreads_buffer[i])
                if i < len(self.bid_quantities_buffer):
                    bid_quantities_in_window.append(self.bid_quantities_buffer[i])
                if i < len(self.ask_quantities_buffer):
                    ask_quantities_in_window.append(self.ask_quantities_buffer[i])
                if i < len(self.trade_sizes_buffer):
                    trade_sizes_in_window.append(self.trade_sizes_buffer[i])

        return (
            prices_in_window,
            timestamps_in_window,
            ticks_in_window,
            spreads_in_window,
            bid_quantities_in_window,
            ask_quantities_in_window,
            trade_sizes_in_window,
        )

    def _compute_one_second_returns(self, prices: list, timestamps: list) -> list:
        """
        Compute 1-second log returns from prices and timestamps.

        For each price, finds the price approximately 1 second ago and computes log return.
        """
        one_second_returns = []

        for i in range(len(prices)):
            if i == 0:
                continue  # Skip first price (no previous price)

            current_price = prices[i]
            current_time = timestamps[i]

            # Find price approximately 1 second ago
            for j in range(i - 1, -1, -1):
                prev_time = timestamps[j]
                time_diff = (current_time - prev_time).total_seconds()

                # If we find a price within 0.5-1.5 seconds, use it
                if 0.5 <= time_diff <= 1.5:
                    prev_price = prices[j]
                    if prev_price > 0 and current_price > 0:
                        log_return = np.log(current_price / prev_price)
                        one_second_returns.append(log_return)
                    break

        return one_second_returns

    def _compute_one_second_price_changes(self, prices: list, timestamps: list) -> list:
        """
        Compute absolute 1-second price changes from prices and timestamps.
        """
        one_second_changes = []

        for i in range(len(prices)):
            if i == 0:
                continue  # Skip first price

            current_price = prices[i]
            current_time = timestamps[i]

            # Find price approximately 1 second ago
            for j in range(i - 1, -1, -1):
                prev_time = timestamps[j]
                time_diff = (current_time - prev_time).total_seconds()

                # If we find a price within 0.5-1.5 seconds, use it
                if 0.5 <= time_diff <= 1.5:
                    prev_price = prices[j]
                    abs_change = abs(current_price - prev_price)
                    one_second_changes.append(abs_change)
                    break

        return one_second_changes

    def _validate_timestamp_order(self, new_timestamp: pd.Timestamp) -> bool:
        """
        Validate that timestamps are in chronological order.

        Args:
            new_timestamp: New timestamp to validate

        Returns:
            True if valid (monotonic or within tolerance), False otherwise
        """
        if len(self.timestamps_buffer) == 0:
            return True

        last_timestamp = self.timestamps_buffer[-1]

        # Allow small backward jumps (e.g., due to clock drift) but log warnings
        if new_timestamp < last_timestamp:
            time_diff = (last_timestamp - new_timestamp).total_seconds()
            if time_diff > 1.0:  # More than 1 second backward
                logger.warning(
                    f"Timestamp out of order: {new_timestamp} < {last_timestamp} (diff: {time_diff:.2f}s)"
                )
                return False

        return True

    def _check_data_quality(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check for NaN, infinite values, and other data quality issues.

        Args:
            features: Dictionary of computed features

        Returns:
            Features dictionary with quality checks applied
        """
        for key, value in features.items():
            if isinstance(value, (int, float)):
                # Check for NaN
                if pd.isna(value):
                    logger.warning(f"NaN detected in feature {key}, replacing with 0.0")
                    features[key] = 0.0
                # Check for infinite values
                elif not np.isfinite(value):
                    logger.warning(
                        f"Infinite value detected in feature {key}, replacing with 0.0"
                    )
                    features[key] = 0.0

        return features

    def compute_features(self, current_tick: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compute all features for the current tick.

        Focused on:
        1. Momentum & Volatility: Log Returns, Realized Volatility, Price Velocity
        2. Liquidity & Microstructure: Bid-Ask Spread, Order Book Imbalance
        3. Activity: Trade Intensity, Volume Velocity

        Returns:
            Dictionary of features
        """
        features = {
            "timestamp": current_tick.get("timestamp", current_tick.get("time")),
            "product_id": current_tick.get("product_id", ""),
            "price": self._get_midprice(current_tick),
            "best_bid": (
                float(current_tick.get("best_bid", 0))
                if current_tick.get("best_bid")
                else None
            ),
            "best_ask": (
                float(current_tick.get("best_ask", 0))
                if current_tick.get("best_ask")
                else None
            ),
            "spread": self._get_spread(current_tick),
            "spread_bps": self._get_spread_bps(current_tick),
        }

        current_price = self._get_midprice(current_tick)
        current_time = (
            self.timestamps_buffer[-1] if len(self.timestamps_buffer) > 0 else None
        )

        # Compute windowed features
        for window in self.window_sizes:
            (
                prices,
                timestamps,
                ticks,
                spreads,
                bid_quantities,
                ask_quantities,
                trade_sizes,
            ) = self._get_window_data(window)

            if len(prices) > 1 and current_price:
                # ============================================================
                # 1. MOMENTUM & VOLATILITY (Price Trends)
                # ============================================================

                # Feature: Log Returns (with fixed lookback periods)
                # Calculate log return over the window: log(current_price / price_window_start)
                window_start_price = prices[0]
                if window_start_price > 0:
                    log_return = np.log(current_price / window_start_price)
                    features[f"log_return_{window}s"] = float(log_return)
                else:
                    features[f"log_return_{window}s"] = 0.0

                # Feature: Realized Volatility (Target Proxy)
                # Rolling standard deviation of 1-second returns over the window
                one_sec_returns = self._compute_one_second_returns(prices, timestamps)
                if len(one_sec_returns) > 1:
                    features[f"realized_volatility_{window}s"] = float(
                        np.std(one_sec_returns)
                    )
                elif len(one_sec_returns) == 1:
                    features[f"realized_volatility_{window}s"] = 0.0
                else:
                    # Fallback: compute from window returns if 1-second returns not available
                    log_prices = np.log(prices)
                    log_returns = np.diff(log_prices)
                    if len(log_returns) > 0:
                        features[f"realized_volatility_{window}s"] = float(
                            np.std(log_returns)
                        )
                    else:
                        features[f"realized_volatility_{window}s"] = 0.0

                # Feature: Price Velocity
                # Rolling mean of absolute 1-second price changes
                one_sec_price_changes = self._compute_one_second_price_changes(
                    prices, timestamps
                )
                if len(one_sec_price_changes) > 0:
                    features[f"price_velocity_{window}s"] = float(
                        np.mean(one_sec_price_changes)
                    )
                else:
                    # Fallback: compute from window price changes
                    if len(prices) > 1:
                        abs_changes = [
                            abs(prices[i] - prices[i - 1])
                            for i in range(1, len(prices))
                        ]
                        if abs_changes:
                            features[f"price_velocity_{window}s"] = float(
                                np.mean(abs_changes)
                            )
                        else:
                            features[f"price_velocity_{window}s"] = 0.0
                    else:
                        features[f"price_velocity_{window}s"] = 0.0

                # ============================================================
                # 2. LIQUIDITY & MICROSTRUCTURE (Market Nerves)
                # ============================================================

                # Feature: Bid-Ask Spread (Rolling Mean)
                # Already computed as spread_mean, but ensure it's present
                if len(spreads) > 0:
                    features[f"spread_mean_{window}s"] = float(np.mean(spreads))
                else:
                    features[f"spread_mean_{window}s"] = 0.0

                # Feature: Order Book Imbalance (OBI)
                # Ratio of buy volume vs. sell volume at the top of the book
                # OBI = bid_volume / (bid_volume + ask_volume)
                if len(bid_quantities) > 0 and len(ask_quantities) > 0:
                    # Use rolling mean of OBI values
                    obi_values = []
                    for i in range(min(len(bid_quantities), len(ask_quantities))):
                        bid_qty = bid_quantities[i]
                        ask_qty = ask_quantities[i]
                        total_qty = bid_qty + ask_qty
                        if total_qty > 0:
                            obi = bid_qty / total_qty
                            obi_values.append(obi)

                    if obi_values:
                        features[f"order_book_imbalance_{window}s"] = float(
                            np.mean(obi_values)
                        )
                    else:
                        features[f"order_book_imbalance_{window}s"] = (
                            0.5  # Neutral (50/50)
                        )
                else:
                    features[f"order_book_imbalance_{window}s"] = (
                        0.5  # Neutral when data unavailable
                    )

                # ============================================================
                # 3. ACTIVITY (Market Energy)
                # ============================================================

                # Feature: Trade Intensity (Rolling Sum of Tick Count)
                # Total number of trades (messages) received in the window
                features[f"trade_intensity_{window}s"] = len(ticks)

                # Feature: Volume Velocity
                # Rolling sum of trade sizes over the window
                if len(trade_sizes) > 0:
                    features[f"volume_velocity_{window}s"] = float(np.sum(trade_sizes))
                else:
                    # Trade size not available in ticker channel, set to 0
                    features[f"volume_velocity_{window}s"] = 0.0

            else:
                # Not enough data for this window - set all features to default values
                features[f"log_return_{window}s"] = 0.0
                features[f"realized_volatility_{window}s"] = 0.0
                features[f"price_velocity_{window}s"] = 0.0
                features[f"spread_mean_{window}s"] = 0.0
                features[f"order_book_imbalance_{window}s"] = 0.5
                features[f"trade_intensity_{window}s"] = 0
                features[f"volume_velocity_{window}s"] = 0.0

        # Time since last trade (activity feature)
        if self.last_tick_time is not None and len(self.timestamps_buffer) > 1:
            current_time = self.timestamps_buffer[-1]
            prev_time = self.timestamps_buffer[-2]
            time_since = (current_time - prev_time).total_seconds()
            features["time_since_last_trade"] = float(time_since)
        else:
            features["time_since_last_trade"] = 0.0

        # Gap detection (for monitoring and potential forward-fill)
        if len(self.timestamps_buffer) > 1:
            current_time = self.timestamps_buffer[-1]
            prev_time = self.timestamps_buffer[-2]
            gap_seconds = (current_time - prev_time).total_seconds()
            features["gap_seconds"] = float(gap_seconds)

            # Log large gaps (potential data quality issue)
            if gap_seconds > 10.0:  # More than 10 seconds gap
                logger.warning(f"Large gap detected: {gap_seconds:.2f}s between ticks")
        else:
            features["gap_seconds"] = 0.0

        # Data quality checks
        features = self._check_data_quality(features)

        return features


class FeaturePipeline:
    """Main feature pipeline: consume from Kafka, compute features, publish and save."""

    def __init__(
        self,
        input_topic: str = "ticks.raw",
        output_topic: str = "ticks.features",
        bootstrap_servers: str = "localhost:9092",
        output_file: str = "data/processed/features.parquet",
        window_sizes: list = [30, 60, 300],
        create_kafka: bool = True,
        add_labels: bool = True,
        label_threshold_percentile: int = 90,
        label_gap_threshold_seconds: int = 300,
    ):
        """Initialize the feature pipeline.

        Args:
            input_topic: Kafka topic to consume from
            output_topic: Kafka topic to publish to
            bootstrap_servers: Kafka bootstrap servers
            output_file: Path to output parquet file
            window_sizes: List of window sizes in seconds for feature computation
            create_kafka: Whether to create Kafka consumer/producer (False for tests)
            add_labels: Whether to automatically add volatility_spike labels to output
            label_threshold_percentile: Percentile to use as threshold for labels (default: 90)
            label_gap_threshold_seconds: Time gap (seconds) that indicates a new data chunk (default: 300 = 5 min)
        """

        self.input_topic = input_topic
        self.output_topic = output_topic
        self.output_file = output_file
        self.bootstrap_servers = bootstrap_servers
        self.add_labels = add_labels
        self.label_threshold_percentile = label_threshold_percentile
        self.label_gap_threshold_seconds = label_gap_threshold_seconds

        # Create output directory
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        # State management for graceful shutdown and reconnection
        self.running = True
        self.shutdown_event = threading.Event()
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_base_delay = 2  # Start with 2 seconds
        
        # Message counters for cleanup reporting
        self.message_count = 0
        self.processed_count = 0
        self.skipped_count = 0

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Start Prometheus metrics server for consumer lag (if enabled)
        self.metrics_port = int(os.getenv("FEATURIZER_METRICS_PORT", "8001"))
        self.consumer_group = "feature-pipeline"
        if _HAS_KAFKA_METRICS and create_kafka:
            try:
                start_metrics_server(self.metrics_port)
            except Exception as e:
                logger.warning(f"Failed to start metrics server: {e}")

        # Initialize Kafka consumer/producer if requested (set False for unit tests)
        if create_kafka:
            self._init_kafka_connections()
        else:
            # For tests we leave consumer/producer as None (or user may inject fakes)
            self.consumer = None
            self.producer = None

        # Initialize feature computer
        self.feature_computer = FeatureComputer(window_sizes=window_sizes)

        # Buffer for batch writing to parquet
        self.features_batch = []
        self.batch_size = 100

        logger.info("FeaturePipeline initialized")
        logger.info(f"  Input topic: {input_topic}")
        logger.info(f"  Output topic: {output_topic}")
        logger.info(f"  Output file: {output_file}")
        logger.info(f"  Add labels: {add_labels}")
        if add_labels:
            logger.info(f"  Label threshold: {label_threshold_percentile}th percentile")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False
        self.shutdown_event.set()

    def _init_kafka_connections(self):
        """Initialize Kafka consumer and producer with retry logic"""
        KafkaConsumer, KafkaProducer, KafkaError, KafkaTimeoutError = _import_kafka()
        max_retries = 5
        base_delay = 2

        # Retry logic for consumer
        for attempt in range(max_retries):
            try:
                if hasattr(self, 'consumer') and self.consumer:
                    try:
                        self.consumer.close()
                    except Exception:
                        pass

                self.consumer = KafkaConsumer(
                    self.input_topic,
                    bootstrap_servers=self.bootstrap_servers,
                    value_deserializer=self._safe_json_deserializer,
                    auto_offset_reset="earliest",
                    group_id=self.consumer_group,
                    enable_auto_commit=True,
                )
                logger.info(f"✓ Connected to Kafka consumer at {self.bootstrap_servers}")
                
                # Set consumer connected status
                if _HAS_KAFKA_METRICS:
                    set_consumer_connected(self.consumer_group, self.input_topic, True)
                
                break

            except (KafkaError, KafkaTimeoutError, Exception) as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Kafka consumer connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Failed to connect Kafka consumer after {max_retries} attempts: {e}"
                    )
                    raise

        # Retry logic for producer
        for attempt in range(max_retries):
            try:
                if hasattr(self, 'producer') and self.producer:
                    try:
                        self.producer.close(timeout=5)
                    except Exception:
                        pass

                self.producer = KafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    acks=1,
                    retries=3,
                    max_in_flight_requests_per_connection=5,
                    request_timeout_ms=30000,
                )
                logger.info(f"✓ Connected to Kafka producer at {self.bootstrap_servers}")
                self.reconnect_attempts = 0  # Reset on success
                return

            except (KafkaError, KafkaTimeoutError, Exception) as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Kafka producer connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Failed to connect Kafka producer after {max_retries} attempts: {e}"
                    )
                    raise

    def _reconnect_kafka(self):
        """Reconnect to Kafka with exponential backoff"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(
                f"Max reconnection attempts ({self.max_reconnect_attempts}) reached"
            )
            return False

        delay = self.reconnect_base_delay * (
            2 ** min(self.reconnect_attempts, 6)
        )  # Cap at 64 seconds
        logger.warning(
            f"Reconnecting to Kafka (attempt {self.reconnect_attempts + 1}/{self.max_reconnect_attempts}) in {delay}s..."
        )
        time.sleep(delay)

        try:
            self._init_kafka_connections()
            self.reconnect_attempts = 0
            return True
        except Exception as e:
            self.reconnect_attempts += 1
            logger.error(f"Reconnection failed: {e}")
            return False

    @staticmethod
    def _safe_json_deserializer(message_bytes):
        """Safely deserialize JSON, handling errors gracefully."""
        if message_bytes is None or len(message_bytes) == 0:
            logger.warning("Received empty message, skipping")
            return None
        try:
            decoded = message_bytes.decode("utf-8").strip()
            if not decoded:
                logger.warning("Received whitespace-only message, skipping")
                return None
            return json.loads(decoded)
        except json.JSONDecodeError as e:
            logger.warning(
                f"JSON decode error: {e}. Raw bytes (first 100): {message_bytes[:100]}"
            )
            return None
        except UnicodeDecodeError as e:
            logger.warning(f"Unicode decode error: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected deserialization error: {e}")
            return None

    def process_message(self, message):
        """Process a single message from Kafka."""
        try:
            # Debug: log message metadata (topic/partition/offset) and length
            try:
                topic = getattr(message, "topic", None)
                partition = getattr(message, "partition", None)
                offset = getattr(message, "offset", None)
                raw_value = getattr(message, "value", None)
                val_len = len(raw_value) if raw_value is not None else 0
                logger.debug(
                    f"Received message: topic={topic} partition={partition} offset={offset} value_len={val_len}"
                )
                if logger.isEnabledFor(logging.DEBUG) and isinstance(
                    raw_value, (bytes, bytearray)
                ):
                    # show a short preview
                    preview = raw_value[:200]
                    try:
                        logger.debug(
                            f"Raw preview: {preview.decode('utf-8', errors='replace')}"
                        )
                    except Exception:
                        logger.debug(f"Raw preview bytes: {preview}")
            except Exception:
                # Don't allow logging issues to stop processing
                pass

            tick = message.value

            # Skip if deserialization failed
            if tick is None:
                return None

            # Add tick to feature computer
            self.feature_computer.add_tick(tick)

            # Compute features
            features = self.feature_computer.compute_features(tick)

            # Publish to output topic with reconnection handling
            try:
                self.producer.send(self.output_topic, value=features)
            except Exception as e:
                logger.error(f"Kafka send error: {e}, attempting reconnection...")
                if self._reconnect_kafka():
                    # Retry sending after reconnection
                    try:
                        self.producer.send(self.output_topic, value=features)
                    except Exception as retry_error:
                        logger.error(
                            f"Failed to send after reconnection: {retry_error}"
                        )
                        return None
                else:
                    logger.error("Failed to reconnect, skipping message")
                    return None

            # Add to batch for file writing
            self.features_batch.append(features)

            # Write batch to file periodically
            if len(self.features_batch) >= self.batch_size:
                self._write_batch()

            return features

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return None

    @staticmethod
    def _add_labels_to_dataframe(
        df: pd.DataFrame,
        threshold_percentile: int = 90,
        gap_threshold_seconds: int = 300,
    ) -> pd.DataFrame:
        """
        Add volatility_spike labels to a features dataframe.

        Computes forward-looking volatility (60-second horizon) and creates binary labels
        based on a percentile threshold. Only calculates volatility within each data chunk
        (separated by gaps larger than gap_threshold_seconds) to avoid incorrect cross-chunk
        volatility calculations.

        Args:
            df: Features dataframe (must have 'timestamp' and 'price' columns)
            threshold_percentile: Percentile to use as threshold (default: 90)
            gap_threshold_seconds: Time gap (seconds) that indicates a new data chunk (default: 300 = 5 min)

        Returns:
            DataFrame with 'volatility_spike' column added
        """
        if "volatility_spike" in df.columns:
            logger.info(
                "Dataframe already has 'volatility_spike' column, skipping label creation"
            )
            return df

        # Convert timestamp to datetime if needed
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df = df.copy()
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Sort by timestamp to ensure correct ordering
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Identify data chunks by detecting large gaps
        df = df.copy()
        df["time_diff"] = df["timestamp"].diff().dt.total_seconds()
        df["chunk_id"] = (df["time_diff"] > gap_threshold_seconds).cumsum()

        num_chunks = df["chunk_id"].nunique()
        logger.info(
            f"Detected {num_chunks} data chunk(s) (gaps > {gap_threshold_seconds}s indicate new chunk)"
        )

        if num_chunks > 1:
            chunk_sizes = df.groupby("chunk_id").size()
            logger.info(f"Chunk sizes: {dict(chunk_sizes)}")

        # Compute forward-looking volatility (60-second horizon) within each chunk
        HORIZON_SECONDS = 60

        # Initialize future_volatility column
        df["future_volatility"] = np.nan

        # Process each chunk separately
        all_valid_volatilities = []

        for chunk_id in df["chunk_id"].unique():
            chunk_mask = df["chunk_id"] == chunk_id
            chunk_df = df[chunk_mask].copy()

            if len(chunk_df) < 2:
                logger.debug(f"Chunk {chunk_id} has < 2 rows, skipping")
                continue

            # Compute forward-looking volatility (60-second horizon) for each row
            # For each timestamp t, find all ticks in the next 60 seconds and compute std of returns
            chunk_df = chunk_df.sort_values("timestamp").reset_index(drop=True)
            chunk_df["future_volatility"] = np.nan

            for idx in range(len(chunk_df)):
                current_time = chunk_df.loc[idx, "timestamp"]
                future_time_limit = current_time + pd.Timedelta(seconds=HORIZON_SECONDS)

                # Find all ticks in the future 60-second window
                future_mask = (chunk_df["timestamp"] > current_time) & (
                    chunk_df["timestamp"] <= future_time_limit
                )
                future_ticks = chunk_df[future_mask]

                if len(future_ticks) < 2:
                    # Need at least 2 ticks to compute volatility
                    continue

                # Compute returns (price changes) between consecutive ticks in future window
                future_prices = future_ticks["price"].values
                future_returns = (
                    np.diff(future_prices) / future_prices[:-1]
                )  # (p_t+1 - p_t) / p_t

                # Compute standard deviation of future returns
                if len(future_returns) > 0:
                    chunk_df.loc[idx, "future_volatility"] = np.std(future_returns)

            logger.debug(
                f"Chunk {chunk_id}: Computed future volatility for {chunk_df['future_volatility'].notna().sum()} rows"
            )

            # Update the main dataframe with this chunk's volatility values
            df.loc[chunk_mask, "future_volatility"] = chunk_df[
                "future_volatility"
            ].values

            # Collect valid volatility values for threshold calculation
            valid_volatilities = chunk_df["future_volatility"].dropna()
            all_valid_volatilities.extend(valid_volatilities.tolist())

        # Drop NaN values at chunk boundaries and ends
        df_clean = df.dropna(subset=["future_volatility"]).copy()

        if len(df_clean) == 0:
            logger.warning(
                "No valid rows after computing future volatility, cannot add labels"
            )
            return df

        if len(all_valid_volatilities) == 0:
            logger.warning("No valid volatility values computed, cannot add labels")
            return df

        logger.info(
            f"After computing future volatility: {len(df_clean)} valid rows "
            f"(dropped {len(df) - len(df_clean)} rows)"
        )
        logger.info(
            f"Computed {len(all_valid_volatilities)} valid volatility values across {num_chunks} chunk(s)"
        )

        # Calculate threshold using all valid volatility values (across all chunks)
        THRESHOLD = np.percentile(all_valid_volatilities, threshold_percentile)
        logger.info(
            f"Selected threshold: {THRESHOLD:.6f} ({threshold_percentile}th percentile)"
        )

        # Create binary labels
        df_clean["volatility_spike"] = (
            df_clean["future_volatility"] >= THRESHOLD
        ).astype(int)

        # Class distribution
        label_counts = df_clean["volatility_spike"].value_counts()
        logger.info(
            f"Class distribution: "
            f"No Spike (0): {label_counts.get(0, 0)} ({label_counts.get(0, 0)/len(df_clean)*100:.1f}%), "
            f"Spike (1): {label_counts.get(1, 0)} ({label_counts.get(1, 0)/len(df_clean)*100:.1f}%)"
        )

        # Drop temporary columns
        df_clean = df_clean.drop(
            columns=["future_volatility", "time_diff", "chunk_id"], errors="ignore"
        )

        return df_clean

    def _write_batch(self, add_labels_final: bool = False):
        """
        Write accumulated features to parquet file.

        Note: This method only processes data from Kafka (streaming). It does NOT
        read unprocessed data from files. It only writes newly computed features
        from Kafka messages to the output parquet file.

        Args:
            add_labels_final: If True, add labels to the complete dataset before writing
        """
        if not self.features_batch:
            return

        try:
            df = pd.DataFrame(self.features_batch)

            # Append to existing file or create new one
            # NOTE: This appends Kafka-processed data to existing file.
            # The featurizer ONLY processes data from Kafka, never reads from files.
            if Path(self.output_file).exists():
                existing_df = pd.read_parquet(self.output_file)

                # Basic deduplication: remove rows with identical timestamp+product_id
                # This prevents duplicates if the featurizer restarts and reprocesses Kafka messages
                if (
                    "timestamp" in existing_df.columns
                    and "product_id" in existing_df.columns
                ):
                    # Check for duplicates before appending
                    existing_keys = set(
                        zip(
                            pd.to_datetime(existing_df["timestamp"]),
                            existing_df["product_id"],
                        )
                    )
                    new_keys = set(
                        zip(pd.to_datetime(df["timestamp"]), df["product_id"])
                    )
                    duplicates = new_keys & existing_keys

                    if duplicates:
                        logger.warning(
                            f"Found {len(duplicates)} duplicate rows (same timestamp+product_id), skipping them"
                        )
                        # Filter out duplicates from new data
                        df["_key"] = list(
                            zip(pd.to_datetime(df["timestamp"]), df["product_id"])
                        )
                        df = df[~df["_key"].isin(existing_keys)]
                        df = df.drop(columns=["_key"])

                if len(df) > 0:
                    df = pd.concat([existing_df, df], ignore_index=True)
                else:
                    logger.info("All new rows were duplicates, no new data to append")
                    self.features_batch = []
                    return

            # Add labels if requested (only on final write when we have complete data)
            if add_labels_final and self.add_labels:
                logger.info("Adding volatility_spike labels to complete dataset...")
                df = self._add_labels_to_dataframe(
                    df,
                    self.label_threshold_percentile,
                    self.label_gap_threshold_seconds,
                )

            df.to_parquet(self.output_file, index=False)
            logger.info(
                f"Wrote {len(self.features_batch)} features to {self.output_file}"
            )

            self.features_batch = []

        except Exception as e:
            logger.error(f"Error writing batch: {e}")

    def run(self):
        """Run the feature pipeline with reconnection support."""
        logger.info("Starting feature pipeline...")
        self.message_count = 0
        self.processed_count = 0
        self.skipped_count = 0

        try:
            while self.running and not self.shutdown_event.is_set():
                try:
                    # Use poll() instead of iterating directly to allow reconnection
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
                                    end_offsets = self.consumer.end_offsets([topic_partition])
                                    end_offset = end_offsets.get(topic_partition, committed)
                                    
                                    # Update metrics
                                    update_consumer_lag(
                                        consumer_group=self.consumer_group,
                                        topic=topic_partition.topic,
                                        partition=topic_partition.partition,
                                        consumer_offset=committed,
                                        end_offset=end_offset,
                                    )
                            except Exception as e:
                                logger.debug(f"Failed to update consumer lag metrics: {e}")
                        
                        for message in messages:
                            if not self.running or self.shutdown_event.is_set():
                                break

                            self.message_count += 1
                            features = self.process_message(message)

                            if features:
                                self.processed_count += 1
                                if self.processed_count % 100 == 0:
                                    logger.info(
                                        f"Processed {self.processed_count} messages (total seen: {self.message_count}, skipped: {self.skipped_count})"
                                    )
                            else:
                                self.skipped_count += 1
                                if self.skipped_count % 10 == 0:
                                    logger.warning(
                                        f"Skipped {self.skipped_count} invalid messages so far"
                                    )

                except KeyboardInterrupt:
                    logger.info("Interrupted by user")
                    self.running = False
                    break
                except Exception as e:
                    logger.error(f"Error in message loop: {e}")
                    # Attempt reconnection on Kafka errors
                    try:
                        from kafka.errors import KafkaError, KafkaTimeoutError
                        is_kafka_error = isinstance(e, (KafkaError, KafkaTimeoutError))
                    except ImportError:
                        # If we can't import, check error message for Kafka-related strings
                        error_str = str(e).lower()
                        is_kafka_error = any(
                            keyword in error_str
                            for keyword in ["kafka", "broker", "connection", "timeout"]
                        )

                    if is_kafka_error and self.running:
                        logger.warning("Kafka connection error, attempting reconnection...")
                        if not self._reconnect_kafka():
                            logger.error("Failed to reconnect, waiting before retry...")
                            time.sleep(5)
                    else:
                        # Non-Kafka error, wait briefly and continue
                        time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Shutting down gracefully...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Cleanup resources gracefully"""
        logger.info("Starting graceful cleanup...")

        # Write any remaining features with labels if enabled
        self._write_batch(add_labels_final=self.add_labels)

        # If labels weren't added during batch write, add them now to the complete file
        if self.add_labels and Path(self.output_file).exists():
            try:
                logger.info("Adding labels to final output file...")
                df = pd.read_parquet(self.output_file)
                if "volatility_spike" not in df.columns:
                    df_labeled = self._add_labels_to_dataframe(
                        df,
                        self.label_threshold_percentile,
                        self.label_gap_threshold_seconds,
                    )
                    df_labeled.to_parquet(self.output_file, index=False)
                    logger.info(f"✓ Labels added to {self.output_file}")
                else:
                    logger.info("Labels already present in output file")
            except Exception as e:
                logger.error(f"Error adding labels to final file: {e}")

        # Close Kafka consumer
        if self.consumer:
            try:
                # Set consumer disconnected status
                if _HAS_KAFKA_METRICS:
                    set_consumer_connected(self.consumer_group, self.input_topic, False)
                
                logger.info("Closing Kafka consumer...")
                self.consumer.close(timeout=10)
                logger.info("✓ Kafka consumer closed")
            except Exception as e:
                logger.warning(f"Error closing Kafka consumer: {e}")

        # Flush and close Kafka producer
        if hasattr(self, 'producer') and self.producer:
            try:
                logger.info("Flushing Kafka producer...")
                self.producer.flush(timeout=10)
                logger.info("Closing Kafka producer...")
                self.producer.close(timeout=10)
                logger.info("✓ Kafka producer closed")
            except Exception as e:
                logger.warning(f"Error closing Kafka producer: {e}")

            logger.info("Pipeline stopped.")
        logger.info(f"  Total messages seen: {self.message_count}")
        logger.info(f"  Successfully processed: {self.processed_count}")
        logger.info(f"  Skipped (invalid): {self.skipped_count}")


def add_labels_to_file(
    features_path: str,
    output_path: str = None,
    threshold_percentile: int = 90,
    gap_threshold_seconds: int = 300,
):
    """
    Add volatility_spike labels to an existing features parquet file.

    This is a convenience function that can be used standalone or called from scripts.
    Only calculates volatility within each data chunk (separated by gaps).

    Args:
        features_path: Path to features parquet file (without labels)
        output_path: Path to save labeled features (default: adds '_labeled' suffix)
        threshold_percentile: Percentile to use as threshold (default: 90)
        gap_threshold_seconds: Time gap (seconds) that indicates a new data chunk (default: 300 = 5 min)

    Returns:
        Path to the output file with labels
    """
    logger.info(f"Loading features from {features_path}")
    df = pd.read_parquet(features_path)

    logger.info(f"Loaded {len(df)} rows")
    logger.info(f"Time range: {df['timestamp'].min()} to {df['timestamp'].max()}")

    # Add labels using the static method
    df_labeled = FeaturePipeline._add_labels_to_dataframe(
        df, threshold_percentile, gap_threshold_seconds
    )

    # Determine output path
    if output_path is None:
        features_path_obj = Path(features_path)
        output_path = (
            features_path_obj.parent / f"{features_path_obj.stem}_labeled.parquet"
        )

    # Save labeled features
    df_labeled.to_parquet(output_path, index=False)
    logger.info(f"✓ Saved labeled dataset to {output_path}")
    logger.info(f"  Shape: {df_labeled.shape}")

    return str(output_path)


def main():
    parser = argparse.ArgumentParser(description="Feature engineering pipeline")
    parser.add_argument("--topic_in", default="ticks.raw", help="Input Kafka topic")
    parser.add_argument(
        "--topic_out", default="ticks.features", help="Output Kafka topic"
    )
    parser.add_argument(
        "--bootstrap_servers", default="localhost:9092", help="Kafka bootstrap servers"
    )
    parser.add_argument(
        "--output_file",
        default="data/processed/features.parquet",
        help="Output parquet file",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[30, 60, 300],
        help="Window sizes in seconds",
    )
    parser.add_argument(
        "--add-labels",
        action="store_true",
        default=True,
        help="Automatically add volatility_spike labels to output (default: True)",
    )
    parser.add_argument(
        "--no-labels",
        dest="add_labels",
        action="store_false",
        help="Do not add labels to output",
    )
    parser.add_argument(
        "--label-threshold-percentile",
        type=int,
        default=90,
        help="Percentile to use as threshold for volatility spike labels (default: 90)",
    )
    parser.add_argument(
        "--label-gap-threshold-seconds",
        type=int,
        default=300,
        help="Time gap (seconds) that indicates a new data chunk for label calculation (default: 300 = 5 min)",
    )

    args = parser.parse_args()

    pipeline = FeaturePipeline(
        input_topic=args.topic_in,
        output_topic=args.topic_out,
        bootstrap_servers=args.bootstrap_servers,
        output_file=args.output_file,
        window_sizes=args.windows,
        add_labels=args.add_labels,
        label_threshold_percentile=args.label_threshold_percentile,
        label_gap_threshold_seconds=args.label_gap_threshold_seconds,
    )

    pipeline.run()


if __name__ == "__main__":
    main()
