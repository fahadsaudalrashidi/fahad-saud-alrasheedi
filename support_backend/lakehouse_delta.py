"""Optional Spark/Delta adapter matching the notebook's MERGE semantics."""

import os
from pathlib import Path
from typing import Any

_spark_session = None


def _spark():
    global _spark_session
    if _spark_session is not None:
        return _spark_session
    try:
        from delta import configure_spark_with_delta_pip
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise RuntimeError(
            "LAKEHOUSE_MODE=delta requires the optional lakehouse extra. "
            "Install with `pip install '.[lakehouse]'`."
        ) from exc

    root = os.getenv("DELTA_LAKEHOUSE_PATH", "data/delta_lakehouse")
    Path(root).mkdir(parents=True, exist_ok=True)
    builder = (
        SparkSession.builder.appName("support-intelligence-lakehouse")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.shuffle.partitions", os.getenv("SPARK_SHUFFLE_PARTITIONS", "4"))
        .config("spark.ui.enabled", "false")
    )
    _spark_session = configure_spark_with_delta_pip(builder).getOrCreate()
    return _spark_session


def persist_delta_layers(raw_records: list[dict], valid_records: list[dict]) -> dict[str, int]:
    """Append Bronze and upsert Silver/Gold using the source notebook's Delta keys."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F

    spark = _spark()
    root = os.getenv("DELTA_LAKEHOUSE_PATH", "data/delta_lakehouse")
    bronze_path = str(Path(root) / "bronze" / "support_tickets")
    silver_path = str(Path(root) / "silver" / "support_tickets")
    gold_path = str(Path(root) / "gold" / "category_metrics")

    if raw_records:
        (
            spark.createDataFrame(raw_records)
            .write.format("delta")
            .mode("append")
            .option("mergeSchema", "true")
            .save(bronze_path)
        )

    if valid_records:
        silver = (
            spark.createDataFrame(valid_records)
            .withColumn("ingested_at", F.current_timestamp())
            .dropDuplicates(["ticket_id"])
        )
        if not DeltaTable.isDeltaTable(spark, silver_path):
            silver.write.format("delta").mode("overwrite").save(silver_path)
        else:
            (
                DeltaTable.forPath(spark, silver_path)
                .alias("target")
                .merge(silver.alias("source"), "target.ticket_id = source.ticket_id")
                .whenMatchedUpdateAll()
                .whenNotMatchedInsertAll()
                .execute()
            )

    silver_count = 0
    gold_count = 0
    if DeltaTable.isDeltaTable(spark, silver_path):
        silver_frame = spark.read.format("delta").load(silver_path)
        silver_count = silver_frame.count()
        gold = (
            silver_frame.withColumn("day", F.to_date("created_at"))
            .groupBy("category", "day")
            .agg(
                F.count("*").alias("ticket_count"),
                F.avg(F.length("agent_response")).alias("avg_response_length"),
            )
        )
        if not DeltaTable.isDeltaTable(spark, gold_path):
            gold.write.format("delta").mode("overwrite").save(gold_path)
        else:
            (
                DeltaTable.forPath(spark, gold_path)
                .alias("target")
                .merge(
                    gold.alias("source"),
                    "target.category = source.category AND target.day = source.day",
                )
                .whenMatchedUpdateAll()
                .whenNotMatchedInsertAll()
                .execute()
            )
        gold_count = spark.read.format("delta").load(gold_path).count()

    bronze_count = (
        spark.read.format("delta").load(bronze_path).count()
        if DeltaTable.isDeltaTable(spark, bronze_path)
        else 0
    )
    return {
        "bronze_count": int(bronze_count),
        "silver_count": int(silver_count),
        "gold_count": int(gold_count),
    }
