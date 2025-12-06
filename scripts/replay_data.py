"""
Replay historical tick data to Kafka for testing and validation.

This script reads NDJSON tick files and replays them to Kafka, either in real-time
(preserving original timestamps) or at accelerated speed for testing.
"""

import json
import logging
import os
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from kafka import KafkaProducer
from kafka.errors import KafkaError, KafkaTimeoutError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC_RAW", "ticks.raw")

# Setup logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class DataReplayer:
    """Replays historical tick data to Kafka."""

    def __init__(self, kafka_topic: str = KAFKA_TOPIC):
        self.kafka_topic = kafka_topic
        self.producer = None
        self.message_count = 0
        self._init_kafka_producer()

    def _init_kafka_producer(self):
        """Initialize Kafka producer with retry logic."""
        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            try:
                self.producer = KafkaProducer(
                    bootstrap_servers=KAFKA_BOOTSTRAP,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    acks=1,
                    retries=3,
                    max_in_flight_requests_per_connection=5,
                    linger_ms=100,
                    batch_size=16384,
                    compression_type="gzip",
                    request_timeout_ms=30000,
                    api_version=(0, 10, 1),
                )
                logger.info(f"✓ Connected to Kafka at {KAFKA_BOOTSTRAP}")
                return
            except (KafkaError, KafkaTimeoutError) as e:
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

    def replay_file(
        self,
        file_path: str,
        speed_multiplier: float = 1.0,
        max_messages: Optional[int] = None,
        update_timestamps: bool = True,
    ):
        """
        Replay data from an NDJSON file to Kafka.

        Args:
            file_path: Path to NDJSON file containing tick data
            speed_multiplier: Replay speed (1.0 = real-time, 10.0 = 10x faster, 0 = no delay)
            max_messages: Maximum number of messages to replay (None = all)
            update_timestamps: If True, update timestamps to current time (useful for testing)
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Data file not found: {file_path}")

        logger.info(f"Starting replay from: {file_path}")
        logger.info(f"Kafka topic: {self.kafka_topic}")
        logger.info(f"Speed multiplier: {speed_multiplier}x")
        logger.info(f"Update timestamps: {update_timestamps}")
        if max_messages:
            logger.info(f"Max messages: {max_messages}")

        self.message_count = 0
        start_time = time.time()
        prev_timestamp = None
        replay_start_time = datetime.now(tz=timezone.utc)

        try:
            with open(file_path, "r") as f:
                for line in f:
                    if max_messages and self.message_count >= max_messages:
                        logger.info(f"Reached max messages limit: {max_messages}")
                        break

                    try:
                        message = json.loads(line.strip())

                        # Update timestamp if requested
                        if update_timestamps:
                            # Calculate time offset from start of replay
                            if prev_timestamp:
                                original_timestamp = datetime.fromisoformat(
                                    message["timestamp"].replace("Z", "+00:00")
                                )
                                if self.message_count == 1:
                                    # First message - record the original start time
                                    self.original_start_time = original_timestamp

                                # Calculate elapsed time in original data
                                original_elapsed = (
                                    original_timestamp - self.original_start_time
                                ).total_seconds()

                                # Apply to replay start time
                                new_timestamp = (
                                    replay_start_time.timestamp() + original_elapsed
                                )
                                message["timestamp"] = datetime.fromtimestamp(
                                    new_timestamp, tz=timezone.utc
                                ).isoformat()
                            else:
                                # First message uses current time
                                message["timestamp"] = replay_start_time.isoformat()

                        # Implement replay timing
                        if speed_multiplier > 0 and prev_timestamp:
                            current_timestamp = datetime.fromisoformat(
                                message["timestamp"].replace("Z", "+00:00")
                            )
                            time_diff = (
                                current_timestamp - prev_timestamp
                            ).total_seconds()
                            sleep_time = time_diff / speed_multiplier
                            if sleep_time > 0:
                                time.sleep(sleep_time)

                        # Send to Kafka
                        future = self.producer.send(self.kafka_topic, message)
                        future.get(timeout=10)  # Wait for confirmation

                        self.message_count += 1
                        prev_timestamp = datetime.fromisoformat(
                            message["timestamp"].replace("Z", "+00:00")
                        )

                        # Log progress every 100 messages
                        if self.message_count % 100 == 0:
                            elapsed = time.time() - start_time
                            rate = self.message_count / elapsed if elapsed > 0 else 0
                            logger.info(
                                f"Replayed {self.message_count} messages ({rate:.2f} msg/s)"
                            )

                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipping invalid JSON line: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Error replaying message: {e}")
                        continue

        except KeyboardInterrupt:
            logger.info("Replay interrupted by user")
        finally:
            # Flush producer
            if self.producer:
                logger.info("Flushing remaining messages...")
                self.producer.flush(timeout=10)

            elapsed = time.time() - start_time
            rate = self.message_count / elapsed if elapsed > 0 else 0
            logger.info(
                f"✓ Replay complete: {self.message_count} messages in {elapsed:.2f}s ({rate:.2f} msg/s)"
            )

    def cleanup(self):
        """Cleanup Kafka producer."""
        if self.producer:
            try:
                self.producer.close(timeout=10)
                logger.info("✓ Kafka producer closed")
            except Exception as e:
                logger.warning(f"Error closing producer: {e}")


def main():
    parser = argparse.ArgumentParser(description="Replay historical tick data to Kafka")
    parser.add_argument(
        "--file",
        type=str,
        required=True,
        help="Path to NDJSON file to replay",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier (1.0=real-time, 10.0=10x faster, 0=no delay)",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        help="Maximum number of messages to replay (default: all)",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=KAFKA_TOPIC,
        help=f"Kafka topic to publish to (default: {KAFKA_TOPIC})",
    )
    parser.add_argument(
        "--preserve-timestamps",
        action="store_true",
        help="Preserve original timestamps instead of updating to current time",
    )

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Kafka Data Replay Tool")
    logger.info("=" * 60)

    replayer = DataReplayer(kafka_topic=args.topic)

    try:
        replayer.replay_file(
            file_path=args.file,
            speed_multiplier=args.speed,
            max_messages=args.max_messages,
            update_timestamps=not args.preserve_timestamps,
        )
    finally:
        replayer.cleanup()


if __name__ == "__main__":
    main()
