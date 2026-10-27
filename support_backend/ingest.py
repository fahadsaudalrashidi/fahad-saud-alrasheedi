"""Ticket transport: deterministic local mode or an explicitly configured Kafka broker."""

import copy
import os
import time

TOPIC = os.getenv("KAFKA_TOPIC", "support-tickets")


def round_trip_tickets(records: list[dict]) -> tuple[list[dict], str]:
    """Publish and consume a batch, or use the in-process transport in local mode."""
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not bootstrap:
        return copy.deepcopy(records), "local"

    try:
        from kafka import KafkaConsumer, KafkaProducer, TopicPartition
    except ImportError as exc:
        raise RuntimeError(
            "KAFKA_BOOTSTRAP_SERVERS is set, but kafka-python-ng is not installed. "
            "Install the streaming extra with `pip install '.[streaming]'`."
        ) from exc

    consumer = KafkaConsumer(
        bootstrap_servers=bootstrap.split(","),
        enable_auto_commit=False,
        value_deserializer=lambda value: __import__("json").loads(value.decode("utf-8")),
        consumer_timeout_ms=1000,
    )
    producer = None
    try:
        partition_ids = consumer.partitions_for_topic(TOPIC)
        if not partition_ids:
            raise RuntimeError(
                f"Kafka topic {TOPIC!r} is not available; create it before running the pipeline."
            )
        partitions = [TopicPartition(TOPIC, partition) for partition in partition_ids]
        consumer.assign(partitions)
        start_offsets = consumer.end_offsets(partitions)
        producer = KafkaProducer(
            bootstrap_servers=bootstrap.split(","),
            value_serializer=lambda value: __import__("json").dumps(value).encode("utf-8"),
            key_serializer=lambda key: key.encode("utf-8") if key else None,
        )
        for record in records:
            producer.send(TOPIC, key=record.get("ticket_id"), value=record)
        producer.flush(timeout=30)
        for partition, offset in start_offsets.items():
            consumer.seek(partition, offset)

        consumed: list[dict] = []
        deadline = time.monotonic() + 30
        while len(consumed) < len(records) and time.monotonic() < deadline:
            for messages in consumer.poll(timeout_ms=500).values():
                consumed.extend(message.value for message in messages)
        if len(consumed) != len(records):
            raise TimeoutError(
                f"Kafka round-trip timed out: expected {len(records)} records, received {len(consumed)}."
            )
        return consumed, "kafka"
    finally:
        if producer is not None:
            producer.close()
        consumer.close()
