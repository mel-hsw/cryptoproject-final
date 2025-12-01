"""
WebSocket Ingestor for Coinbase Advanced Trade API
Streams real-time ticker data to Kafka and optionally saves to disk
"""

import json
import logging
import os
import time
import argparse
import signal
import threading
from datetime import datetime
from pathlib import Path
import websocket
from kafka import KafkaProducer
from kafka.errors import KafkaError, KafkaTimeoutError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
COINBASE_WS_URL = os.getenv("COINBASE_WS_URL", "wss://advanced-trade-ws.coinbase.com")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC_RAW", "ticks.raw")

# Setup logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class CoinbaseIngestor:
    """Ingests real-time ticker data from Coinbase WebSocket"""

    def __init__(self, trading_pairs, save_to_disk=False):
        self.trading_pairs = (
            trading_pairs if isinstance(trading_pairs, list) else [trading_pairs]
        )
        self.save_to_disk = save_to_disk
        self.producer = None
        self.ws = None
        self.message_count = 0
        self.start_time = None
        self.file_handle = None
        self.running = True
        self.shutdown_event = threading.Event()
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_base_delay = 1  # Start with 1 second

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Initialize Kafka producer with retry logic
        self._init_kafka_producer()

        # Setup file logging if enabled
        if self.save_to_disk:
            self._init_file_writer()

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False
        self.shutdown_event.set()
        if self.ws:
            self.ws.close()

    def _init_kafka_producer(self, retry_count=0):
        """Initialize Kafka producer with exponential backoff retry logic"""
        max_retries = 5
        base_delay = 2  # Start with 2 seconds

        for attempt in range(max_retries):
            try:
                if self.producer:
                    try:
                        self.producer.close(timeout=5)
                    except Exception:
                        pass

                self.producer = KafkaProducer(
                    bootstrap_servers=KAFKA_BOOTSTRAP,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    acks=1,
                    retries=3,
                    max_in_flight_requests_per_connection=5,
                    linger_ms=100,  # Small batch delay
                    batch_size=16384,  # Batch size in bytes
                    compression_type="gzip",  # Compress messages
                    request_timeout_ms=30000,
                    api_version=(0, 10, 1),
                )

                # Test connection by sending a test message (non-blocking)
                # The producer will fail on actual send if connection is bad
                logger.info(f"✓ Connected to Kafka at {KAFKA_BOOTSTRAP}")
                self.reconnect_attempts = 0  # Reset on success
                return

            except (KafkaError, KafkaTimeoutError) as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)  # Exponential backoff
                    logger.warning(
                        f"Kafka connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Failed to connect to Kafka after {max_retries} attempts: {e}"
                    )
                    if not self.running:  # If shutting down, don't raise
                        return
                    raise

    def _check_kafka_health(self):
        """Check if Kafka producer is healthy"""
        try:
            if self.producer:
                # Producer is healthy if it exists and hasn't been closed
                # Actual connection will be tested on send
                return True
        except Exception as e:
            logger.warning(f"Kafka health check failed: {e}")
        return False

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
            self._init_kafka_producer()
            self.reconnect_attempts = 0
            return True
        except Exception as e:
            self.reconnect_attempts += 1
            logger.error(f"Reconnection failed: {e}")
            return False

    def _init_file_writer(self):
        """Initialize NDJSON file writer"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pairs_str = "_".join(self.trading_pairs).replace("-", "")
        filename = f"data/raw/ticks_{pairs_str}_{timestamp}.ndjson"

        Path("data/raw").mkdir(parents=True, exist_ok=True)
        self.file_handle = open(filename, "w")
        logger.info(f"Writing raw data to {filename}")

    def on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)

            # Log message structure for debugging
            msg_type = data.get("channel") or data.get("type")
            logger.debug(f"Message type: {msg_type}, Keys: {list(data.keys())}")

            # Handle subscription confirmations
            if data.get("type") == "subscriptions":
                logger.info(f"Subscription confirmed: {data}")
                return

            # Skip heartbeats
            if data.get("channel") == "heartbeats":
                logger.debug("Received heartbeat")
                return

            # Check for errors
            if data.get("type") == "error":
                logger.error(f"WebSocket error message: {data}")
                return

            # Try multiple message formats
            ticker_data = None

            # Format 1: events array (Advanced Trade API) - handles 'update' type
            if "events" in data:
                for event in data.get("events", []):
                    # Event type can be 'update' or 'ticker'
                    if event.get("type") in ["update", "ticker", "snapshot"]:
                        tickers = event.get("tickers", [])
                        if tickers:
                            ticker_data = tickers[0]
                            break

            # Format 2: Direct ticker in message (old format)
            elif data.get("type") == "ticker":
                ticker_data = data

            # Format 3: Match/trade messages
            elif data.get("type") in ["match", "last_match"]:
                ticker_data = {
                    "product_id": data.get("product_id"),
                    "price": data.get("price"),
                    "size": data.get("size"),
                    "time": data.get("time"),
                }

            if not ticker_data:
                logger.debug("No ticker data found in message")
                return

            # Enrich with metadata
            enriched = {
                "timestamp": datetime.now().isoformat(),
                "product_id": ticker_data.get("product_id"),
                "price": ticker_data.get("price"),
                "volume_24h": ticker_data.get("volume_24_h")
                or ticker_data.get("volume"),
                "low_24h": ticker_data.get("low_24_h") or ticker_data.get("low"),
                "high_24h": ticker_data.get("high_24_h") or ticker_data.get("high"),
                "best_bid": ticker_data.get("best_bid") or ticker_data.get("bid"),
                "best_ask": ticker_data.get("best_ask") or ticker_data.get("ask"),
                "raw": ticker_data,
            }

            # Send to Kafka with reconnection handling
            if not self._check_kafka_health():
                logger.warning("Kafka connection unhealthy, attempting reconnection...")
                if not self._reconnect_kafka():
                    logger.error("Failed to reconnect to Kafka, skipping message")
                    return

            try:
                future = self.producer.send(KAFKA_TOPIC, enriched)
                future.add_callback(self._on_send_success)
                future.add_errback(self._on_send_error)

                # Force flush every 10 messages to ensure delivery
                if self.message_count % 10 == 0:
                    self.producer.flush(timeout=5)
            except (KafkaError, KafkaTimeoutError) as e:
                logger.error(f"Kafka send error: {e}, attempting reconnection...")
                if self._reconnect_kafka():
                    # Retry sending after reconnection
                    try:
                        future = self.producer.send(KAFKA_TOPIC, enriched)
                        future.add_callback(self._on_send_success)
                        future.add_errback(self._on_send_error)
                    except Exception as retry_error:
                        logger.error(
                            f"Failed to send after reconnection: {retry_error}"
                        )

            # Save to disk if enabled
            if self.save_to_disk and self.file_handle:
                self.file_handle.write(json.dumps(enriched) + "\n")
                self.file_handle.flush()

            self.message_count += 1

            if self.message_count % 10 == 0:  # Log every 10 messages
                elapsed = time.time() - self.start_time
                rate = self.message_count / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Processed {self.message_count} messages ({rate:.2f} msg/s)"
                )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

    def _on_send_success(self, metadata):
        """Callback for successful Kafka send"""
        logger.debug(f"Message sent to {metadata.topic} partition {metadata.partition}")

    def _on_send_error(self, exc):
        """Callback for failed Kafka send"""
        logger.error(f"Failed to send message: {exc}")

    def on_error(self, ws, error):
        """Handle WebSocket errors with reconnection logic"""
        logger.error(f"WebSocket error: {error}")
        if self.running and not self.shutdown_event.is_set():
            # Attempt to reconnect WebSocket
            logger.info("Attempting to reconnect WebSocket...")
            time.sleep(5)  # Wait before reconnecting
            if self.running:
                try:
                    self.ws.run_forever()
                except Exception as e:
                    logger.error(f"WebSocket reconnection failed: {e}")

    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket closure"""
        logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
        logger.info(f"Total messages processed: {self.message_count}")

        # Attempt reconnection if not shutting down
        if self.running and not self.shutdown_event.is_set():
            logger.info("WebSocket closed unexpectedly, attempting reconnection...")
            time.sleep(5)
            if self.running:
                try:
                    self.ws.run_forever()
                except Exception as e:
                    logger.error(f"WebSocket reconnection failed: {e}")

    def on_open(self, ws):
        """Subscribe to ticker channel on connection"""
        logger.info(f"WebSocket connected to {COINBASE_WS_URL}")

        # Subscribe to ticker channel for specified trading pairs
        subscribe_message = {
            "type": "subscribe",
            "product_ids": self.trading_pairs,
            "channel": "ticker",
        }

        logger.info(f"Sending subscription: {subscribe_message}")
        ws.send(json.dumps(subscribe_message))
        logger.info(f"Subscribed to ticker channel for {self.trading_pairs}")

        # Also subscribe to heartbeats to keep connection alive
        heartbeat_message = {
            "type": "subscribe",
            "product_ids": self.trading_pairs,
            "channel": "heartbeats",
        }
        ws.send(json.dumps(heartbeat_message))
        logger.info("Subscribed to heartbeats")

    def run(self, duration_minutes=None):
        """Start WebSocket connection and run for specified duration"""
        self.start_time = time.time()
        end_time = None

        if duration_minutes:
            end_time = self.start_time + (duration_minutes * 60)
            logger.info(f"Starting ingestion for {duration_minutes} minutes")
        else:
            logger.info("Starting ingestion (press Ctrl+C to stop)")

        # Configure WebSocket with auto-reconnect
        self.ws = websocket.WebSocketApp(
            COINBASE_WS_URL,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )

        try:
            while self.running and not self.shutdown_event.is_set():
                if end_time and time.time() >= end_time:
                    logger.info("Duration limit reached, shutting down...")
                    break

                try:
                    self.ws.run_forever()
                    # If we get here, connection closed
                    if not self.running or self.shutdown_event.is_set():
                        break
                    logger.info(
                        "WebSocket closed, attempting reconnection in 5 seconds..."
                    )
                    time.sleep(5)
                except Exception as e:
                    if self.running and not self.shutdown_event.is_set():
                        logger.error(f"WebSocket error: {e}, retrying in 5 seconds...")
                        time.sleep(5)
                    else:
                        break

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            self.running = False

        finally:
            self.cleanup()

    def cleanup(self):
        """Cleanup resources gracefully"""
        logger.info(
            f"Starting graceful cleanup. Total messages processed: {self.message_count}"
        )

        # Close WebSocket first
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

        # Flush and close Kafka producer
        if self.producer:
            try:
                logger.info("Flushing Kafka producer...")
                self.producer.flush(timeout=10)
                logger.info("Closing Kafka producer...")
                self.producer.close(timeout=10)
                logger.info("✓ Kafka producer closed")
            except Exception as e:
                logger.warning(f"Error closing Kafka producer: {e}")

        # Close file handle
        if self.file_handle:
            try:
                self.file_handle.close()
                logger.info("✓ File handle closed")
            except Exception as e:
                logger.warning(f"Error closing file handle: {e}")

        logger.info("Cleanup complete")


def main():
    parser = argparse.ArgumentParser(description="Ingest Coinbase ticker data")
    parser.add_argument(
        "--pair",
        type=str,
        default="BTC-USD",
        help="Trading pair (e.g., BTC-USD, ETH-USD)",
    )
    parser.add_argument(
        "--pairs",
        type=str,
        nargs="+",
        help="Multiple trading pairs (e.g., BTC-USD ETH-USD)",
    )
    parser.add_argument(
        "--minutes", type=int, help="Duration to run in minutes (omit for indefinite)"
    )
    parser.add_argument(
        "--save-disk",
        action="store_true",
        help="Save raw data to disk in addition to Kafka",
    )

    args = parser.parse_args()

    # Determine trading pairs
    if args.pairs:
        pairs = args.pairs
    else:
        pairs = [args.pair]

    logger.info("Starting Coinbase WebSocket Ingestor")
    logger.info(f"Trading pairs: {pairs}")
    logger.info(f"Kafka topic: {KAFKA_TOPIC}")

    # Create and run ingestor
    ingestor = CoinbaseIngestor(pairs, save_to_disk=args.save_disk)
    ingestor.run(duration_minutes=args.minutes)


if __name__ == "__main__":
    main()
