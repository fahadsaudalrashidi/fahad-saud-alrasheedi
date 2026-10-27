"""The notebook's ingestion → quality → Bronze/Silver/Gold → lineage pipeline."""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from support_backend.contracts import CATEGORIES, validate_batch
from support_backend.ingest import round_trip_tickets
from support_backend.store import (
    bronze_count,
    insert_bronze,
    record_counts,
    refresh_gold,
    save_pipeline_run,
    silver_records,
    silver_count,
    update_stage,
    upsert_silver,
)

STAGES = [
    "Kafka ingestion",
    "Bronze lakehouse",
    "Silver validation",
    "Gold aggregation",
    "Hybrid RAG index",
]


def _emit_lineage(stage: str, event_type: str, run_id: str, detail: dict | None = None) -> None:
    path = Path(os.getenv("LINEAGE_LOG_PATH", "data/lineage_events.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.getenv("OPENLINEAGE_MODE", "file").lower() == "client":
        try:
            from openlineage.client import OpenLineageClient
            from openlineage.client.event_v2 import Job, Run, RunEvent, RunState
            from openlineage.client.transport.file import FileConfig, FileTransport
        except ImportError as exc:
            raise RuntimeError(
                "OPENLINEAGE_MODE=client requires the optional quality extra. "
                "Install with `pip install '.[quality]'`."
            ) from exc
        state = {
            "START": RunState.START,
            "COMPLETE": RunState.COMPLETE,
            "FAIL": RunState.FAIL,
        }[event_type]
        client = OpenLineageClient(
            transport=FileTransport(FileConfig(log_file_path=str(path)))
        )
        client.emit(
            RunEvent(
                eventType=state,
                eventTime=datetime.now(UTC).isoformat(),
                run=Run(runId=run_id),
                job=Job(namespace="support-intelligence", name=stage),
                producer="support-intelligence-platform",
                inputs=[],
                outputs=[],
            )
        )
        return
    event = {
        "eventType": event_type,
        "eventTime": datetime.now(UTC).isoformat(),
        "run": {"runId": run_id},
        "job": {"namespace": "support-intelligence", "name": stage},
        "producer": "support-intelligence-platform",
        "detail": detail or {},
    }
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(event) + "\n")


def _quality_check() -> bool:
    if os.getenv("QUALITY_MODE", "builtin").lower() == "great-expectations":
        from support_backend.quality import run_great_expectations

        return bool(run_great_expectations(silver_records())["success"])
    from support_backend.store import connect

    with connect() as db:
        count = int(db.execute("SELECT COUNT(*) FROM silver_tickets").fetchone()[0])
        violations = int(
            db.execute(
                """
                SELECT COUNT(*) FROM silver_tickets
                WHERE ticket_id IS NULL OR trim(customer_message) = ''
                   OR length(trim(customer_message)) < 5
                   OR trim(agent_response) = ''
                   OR category NOT IN (?, ?, ?, ?, ?)
                """,
                tuple(sorted(CATEGORIES)),
            ).fetchone()[0]
        )
        duplicates = int(
            db.execute(
                "SELECT COUNT(*) FROM (SELECT ticket_id FROM silver_tickets GROUP BY ticket_id HAVING COUNT(*) > 1)"
            ).fetchone()[0]
        )
    return count == 0 or (violations == 0 and duplicates == 0)


def run_pipeline(
    records: list[dict],
    rebuild_rag: Callable[[], int] | None = None,
) -> dict:
    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC).isoformat()
    accepted: list[dict] = []
    rejected: list[dict] = []

    try:
        update_stage("Kafka ingestion", "healthy", 0, "Publishing the incoming batch")
        _emit_lineage("kafka_ingestion", "START", run_id)
        consumed, mode = round_trip_tickets(records)
        update_stage(
            "Kafka ingestion",
            "healthy",
            len(consumed),
            "Kafka round-trip complete" if mode == "kafka" else "In-process transport (local mode)",
        )
        _emit_lineage("kafka_ingestion", "COMPLETE", run_id, {"records": len(consumed), "mode": mode})

        _emit_lineage("bronze_load", "START", run_id)
        insert_bronze(consumed)
        update_stage(
            "Bronze lakehouse",
            "healthy",
            bronze_count(),
            "Append-only raw records retained, including contract rejections",
        )
        _emit_lineage("bronze_load", "COMPLETE", run_id, {"records": len(consumed)})

        _emit_lineage("silver_contract_validation", "START", run_id)
        accepted, rejected = validate_batch(consumed)
        metadata = {row.get("ticket_id"): row for row in consumed}
        enriched = [
            {
                **row,
                **{
                    key: metadata.get(row["ticket_id"], {}).get(key)
                    for key in (
                        "status",
                        "priority",
                        "quality_status",
                        "sentiment",
                        "response_minutes",
                    )
                },
            }
            for row in accepted
        ]
        upsert_silver(enriched)
        delta_counts = None
        if os.getenv("LAKEHOUSE_MODE", "sqlite").lower() == "delta":
            from support_backend.lakehouse_delta import persist_delta_layers

            delta_counts = persist_delta_layers(consumed, enriched)
        update_stage(
            "Silver validation",
            "healthy" if accepted else "warning",
            delta_counts["silver_count"] if delta_counts else silver_count(),
            f"{len(accepted)} contract-valid rows; {len(rejected)} rejected",
        )
        _emit_lineage(
            "silver_contract_validation",
            "COMPLETE",
            run_id,
            {"accepted": len(accepted), "rejected": len(rejected)},
        )

        _emit_lineage("gold_aggregation", "START", run_id)
        refresh_gold()
        _bronze, _silver, gold_count = record_counts()
        if delta_counts:
            gold_count = delta_counts["gold_count"]
        update_stage(
            "Gold aggregation",
            "healthy",
            gold_count,
            "Category/day rollups upserted from Silver",
        )
        _emit_lineage("gold_aggregation", "COMPLETE", run_id, {"groups": gold_count})

        _emit_lineage("quality_gate", "START", run_id)
        quality_passed = _quality_check()
        update_stage(
            "Quality gate",
            "healthy" if quality_passed else "warning",
            silver_count(),
            "Silver expectations passed" if quality_passed else "Silver expectations found violations",
        )
        _emit_lineage("quality_gate", "COMPLETE" if quality_passed else "FAIL", run_id)

        _emit_lineage("hybrid_rag_index", "START", run_id)
        chunk_count = rebuild_rag() if rebuild_rag else 0
        update_stage(
            "Hybrid RAG index",
            "healthy",
            chunk_count,
            "Knowledge chunks indexed for dense + BM25 retrieval and reranking",
        )
        _emit_lineage("hybrid_rag_index", "COMPLETE", run_id, {"chunks": chunk_count})

        completed_at = datetime.now(UTC).isoformat()
        current_bronze, current_silver, current_gold = record_counts()
        if delta_counts:
            current_bronze = delta_counts["bronze_count"]
            current_silver = delta_counts["silver_count"]
            current_gold = delta_counts["gold_count"]
        result = {
            "run_id": run_id,
            "received": len(consumed),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "bronze_count": current_bronze,
            "silver_count": current_silver,
            "gold_count": current_gold,
            "quality_passed": quality_passed,
            "started_at": started_at,
            "completed_at": completed_at,
        }
        save_pipeline_run(result)
        return result
    except Exception as exc:
        for stage in STAGES:
            update_stage(stage, "warning", 0, f"Pipeline run failed: {type(exc).__name__}")
        _emit_lineage("support_pipeline", "FAIL", run_id, {"error": type(exc).__name__})
        raise
