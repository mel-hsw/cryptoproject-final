"""
Replay raw NDJSON data to Kafka for testing the pipeline.
Useful for testing the feature pipeline and API without live WebSocket connection.
"""

import argparse
import json
import logging
import time
from pathlib import Path
from datetime import datetime

from kafka import KafkaProducer
from kafka.errors import KafkaError

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def replay_to_kafka(
    input_file: str,
    topic: str = "ticks.raw",
    bootstrap_servers: str = "localhost:9092",
    speed_multiplier: float = 1.0,
    max_messages: int = None,
):
    """
    Replay NDJSON file to Kafka topic.

    Args:
        input_file: Path to NDJSON file
        topic: Kafka topic to publish to
        bootstrap_servers: Kafka bootstrap servers
        speed_multiplier: Speed multiplier (1.0 = real-time, 10.0 = 10x speed)
        max_messages: Maximum number of messages to send (None = all)
    """
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    logger.info(f"Replaying {input_file} to Kafka topic {topic}")
    logger.info(f"Speed multiplier: {speed_multiplier}x")

    # Initialize Kafka producer
    try:
        producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks=1,
            retries=3,
        )
        logger.info(f"Connected to Kafka at {bootstrap_servers}")
    except KafkaError as e:
        logger.error(f"Failed to connect to Kafka: {e}")
        raise

    # Read and replay messages
    message_count = 0
    last_timestamp = None

    try:
        with open(input_path, "r") as f:
            for line_num, line in enumerate(f, 1):
                if max_messages and message_count >= max_messages:
                    logger.info(f"Reached max_messages limit ({max_messages})")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    tick = json.loads(line)

                    # Calculate delay based on timestamps (if available)
                    current_timestamp_str = tick.get("timestamp", tick.get("time"))
                    if current_timestamp_str and last_timestamp:
                        try:
                            current_ts = datetime.fromisoformat(
                                current_timestamp_str.replace("Z", "+00:00")
                            )
                            last_ts = datetime.fromisoformat(
                                last_timestamp.replace("Z", "+00:00")
                            )

                            # Calculate real delay
                            real_delay = (current_ts - last_ts).total_seconds()
                            # Adjust by speed multiplier
                            delay = max(0, real_delay / speed_multiplier)

                            if delay > 0:
                                time.sleep(delay)
                        except (ValueError, TypeError):
                            # If timestamp parsing fails, use small fixed delay
                            time.sleep(0.01 / speed_multiplier)
                    else:
                        # No timestamp, use small fixed delay
                        time.sleep(0.01 / speed_multiplier)

                    # Send to Kafka
                    future = producer.send(topic, value=tick)
                    future.get(timeout=10)  # Wait for confirmation

                    message_count += 1
                    last_timestamp = current_timestamp_str

                    if message_count % 100 == 0:
                        logger.info(f"Sent {message_count} messages...")

                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping invalid JSON on line {line_num}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing line {line_num}: {e}")
                    continue

        # Flush remaining messages
        producer.flush(timeout=10)
        logger.info(f"✓ Replay complete: {message_count} messages sent to {topic}")

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        producer.flush(timeout=10)
    finally:
        producer.close()
        logger.info("Kafka producer closed")


def main():
    parser = argparse.ArgumentParser(
        description="Replay raw NDJSON data to Kafka for testing"
    )
    parser.add_argument("--input", required=True, help="Path to NDJSON file")
    parser.add_argument(
        "--topic",
        default="ticks.raw",
        help="Kafka topic to publish to (default: ticks.raw)",
    )
    parser.add_argument(
        "--bootstrap-servers",
        default="localhost:9092",
        help="Kafka bootstrap servers (default: localhost:9092)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Speed multiplier (1.0 = real-time, 10.0 = 10x speed, default: 1.0)",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Maximum number of messages to send (default: all)",
    )

    args = parser.parse_args()

    replay_to_kafka(
        input_file=args.input,
        topic=args.topic,
        bootstrap_servers=args.bootstrap_servers,
        speed_multiplier=args.speed,
        max_messages=args.max_messages,
    )


if __name__ == "__main__":
    main()
