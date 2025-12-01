"""
Kafka Consumer Check Script
Validates that messages are flowing through Kafka topics
"""

import json
import logging
import os
import argparse
from kafka import KafkaConsumer
from kafka.errors import KafkaError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# Setup logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_topic(topic_name, min_messages=1, timeout_ms=30000):
    """
    Check if a Kafka topic has messages

    Args:
        topic_name: Name of the Kafka topic
        min_messages: Minimum number of messages to consume
        timeout_ms: Timeout in milliseconds

    Returns:
        bool: True if minimum messages received, False otherwise
    """
    logger.info(f"Checking topic: {topic_name}")
    logger.info(f"Looking for at least {min_messages} messages")
    logger.info(f"Timeout: {timeout_ms}ms")

    try:
        # Create consumer
        # Do not force JSON deserialization in the KafkaConsumer; handle decoding
        # per-message so we can skip non-JSON or empty payloads gracefully.
        consumer = KafkaConsumer(
            topic_name,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            consumer_timeout_ms=timeout_ms,
            value_deserializer=None,
        )

        logger.info(f"Connected to Kafka at {KAFKA_BOOTSTRAP}")

        # Consume messages. We'll only count messages that are valid JSON values.
        valid_count = 0
        total_seen = 0
        for message in consumer:
            total_seen += 1

            logger.info(f"\n--- Message (seen {total_seen}) ---")
            logger.info(f"Partition: {message.partition}")
            logger.info(f"Offset: {message.offset}")
            logger.info(f"Timestamp: {message.timestamp}")
            logger.info(f"Key: {message.key}")

            # Decode raw bytes to string if necessary
            raw_value = message.value
            try:
                if isinstance(raw_value, (bytes, bytearray)):
                    raw_text = raw_value.decode("utf-8", errors="replace").strip()
                else:
                    raw_text = str(raw_value).strip()
            except Exception:
                raw_text = ""

            if not raw_text:
                logger.warning("Skipping empty message payload")
                continue

            # Try to parse JSON; if it fails, log and skip this record
            try:
                parsed = json.loads(raw_text)
            except Exception as e:
                logger.warning(
                    f"Skipping non-JSON message at offset {message.offset}: {e}"
                )
                logger.debug(f"Raw payload: {raw_text}")
                continue

            # Successful JSON parse
            valid_count += 1
            logger.info("Value:")
            print(json.dumps(parsed, indent=2))

            if valid_count >= min_messages:
                logger.info(
                    f"\n✅ Successfully consumed {valid_count} valid JSON messages (seen {total_seen} total)"
                )
                consumer.close()
                return True

        # Timeout reached — we didn't find enough valid JSON messages
        if valid_count < min_messages:
            logger.warning(
                f"⚠️  Only consumed {valid_count} valid JSON messages (seen {total_seen} total, expected {min_messages})"
            )
            consumer.close()
            return False

        consumer.close()
        return True

    except KafkaError as e:
        logger.error(f"❌ Kafka error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return False


def list_topics():
    """List all available Kafka topics"""
    try:
        consumer = KafkaConsumer(
            bootstrap_servers=KAFKA_BOOTSTRAP, consumer_timeout_ms=5000
        )

        topics = consumer.topics()
        logger.info(f"\n📋 Available topics on {KAFKA_BOOTSTRAP}:")
        for topic in sorted(topics):
            logger.info(f"  - {topic}")

        consumer.close()
        return list(topics)

    except KafkaError as e:
        logger.error(f"❌ Failed to list topics: {e}")
        return []


def get_topic_info(topic_name):
    """Get information about a specific topic"""
    try:
        consumer = KafkaConsumer(
            topic_name, bootstrap_servers=KAFKA_BOOTSTRAP, consumer_timeout_ms=5000
        )

        # Get partitions
        partitions = consumer.partitions_for_topic(topic_name)

        logger.info(f"\n📊 Topic: {topic_name}")
        logger.info(f"Partitions: {len(partitions) if partitions else 0}")

        if partitions:
            # Get beginning and end offsets for each partition
            from kafka import TopicPartition

            for partition in sorted(partitions):
                tp = TopicPartition(topic_name, partition)
                consumer.assign([tp])

                # Get offsets
                beginning = consumer.beginning_offsets([tp])[tp]
                end = consumer.end_offsets([tp])[tp]
                messages = end - beginning

                logger.info(
                    f"  Partition {partition}: {messages} messages (offset {beginning} to {end})"
                )

        consumer.close()

    except KafkaError as e:
        logger.error(f"❌ Failed to get topic info: {e}")


def main():
    parser = argparse.ArgumentParser(description="Check Kafka topic for messages")
    parser.add_argument(
        "--topic",
        type=str,
        default=os.getenv("KAFKA_TOPIC_RAW", "ticks.raw"),
        help="Kafka topic to check",
    )
    parser.add_argument(
        "--min", type=int, default=1, help="Minimum number of messages to consume"
    )
    parser.add_argument(
        "--timeout", type=int, default=30000, help="Timeout in milliseconds"
    )
    parser.add_argument("--list", action="store_true", help="List all available topics")
    parser.add_argument("--info", action="store_true", help="Show topic information")

    args = parser.parse_args()

    # List topics if requested
    if args.list:
        list_topics()
        return

    # Show topic info if requested
    if args.info:
        get_topic_info(args.topic)
        return

    # Check topic for messages
    logger.info("=" * 60)
    logger.info("Kafka Topic Check")
    logger.info("=" * 60)

    success = check_topic(args.topic, args.min, args.timeout)

    if success:
        logger.info("\n✅ Check PASSED")
    else:
        logger.info("\n❌ Check FAILED")
        exit(1)


if __name__ == "__main__":
    main()
