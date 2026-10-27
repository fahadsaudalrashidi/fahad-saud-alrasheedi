"""SQLite persistence for the Bronze, Silver, and Gold serving layers."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from support_backend.contracts import CATEGORIES


def _database_path() -> Path:
    configured = os.getenv("SUPPORT_DB_PATH", "data/support.db")
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(_database_path(), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_store() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS bronze_tickets (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS silver_tickets (
                ticket_id TEXT PRIMARY KEY,
                customer_message TEXT NOT NULL,
                agent_response TEXT NOT NULL,
                category TEXT NOT NULL,
                channel TEXT NOT NULL,
                created_at TEXT NOT NULL,
                kb_reference TEXT,
                status TEXT NOT NULL,
                priority TEXT NOT NULL,
                quality_status TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                response_minutes REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS gold_metrics (
                category TEXT NOT NULL,
                day TEXT NOT NULL,
                ticket_count INTEGER NOT NULL,
                avg_response_length REAL NOT NULL,
                PRIMARY KEY (category, day)
            );
            CREATE TABLE IF NOT EXISTS pipeline_stages (
                name TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                records INTEGER NOT NULL DEFAULT 0,
                last_run_at TEXT,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                received INTEGER NOT NULL,
                accepted INTEGER NOT NULL,
                rejected INTEGER NOT NULL,
                quality_passed INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_articles (
                article_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                source TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS business_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                monthly_tickets INTEGER NOT NULL,
                agent_hourly_cost_sar REAL NOT NULL,
                avg_handle_minutes REAL NOT NULL,
                automation_deflection_pct REAL NOT NULL,
                ai_cost_per_ticket_sar REAL NOT NULL,
                implementation_cost_sar REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS automation_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                risk_score REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        for name in ("Kafka ingestion", "Bronze lakehouse", "Silver validation", "Gold aggregation", "Hybrid RAG index"):
            db.execute(
                """
                INSERT OR IGNORE INTO pipeline_stages(name, status, records, detail)
                VALUES (?, 'idle', 0, 'Waiting for the first pipeline run')
                """,
                (name,),
            )
        from support_backend.data_gen import KB_ARTICLES

        db.executemany(
            """
            INSERT OR IGNORE INTO knowledge_articles(
                article_id, category, title, content, language, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    article["id"],
                    article["category"],
                    article["title"],
                    article["content"],
                    article.get("language", "en"),
                    "source notebook",
                    datetime.now(UTC).isoformat(),
                )
                for article in KB_ARTICLES
            ],
        )
        db.execute(
            """
            INSERT OR IGNORE INTO business_settings(
                id, monthly_tickets, agent_hourly_cost_sar, avg_handle_minutes,
                automation_deflection_pct, ai_cost_per_ticket_sar,
                implementation_cost_sar, updated_at
            ) VALUES (1, 600, 75, 12, 15, 0.75, 0, ?)
            """,
            (datetime.now(UTC).isoformat(),),
        )


def bronze_count() -> int:
    with connect() as db:
        return int(db.execute("SELECT COUNT(*) FROM bronze_tickets").fetchone()[0])


def silver_count() -> int:
    with connect() as db:
        return int(db.execute("SELECT COUNT(*) FROM silver_tickets").fetchone()[0])


def insert_bronze(records: list[dict]) -> None:
    timestamp = datetime.now(UTC).isoformat()
    with connect() as db:
        db.executemany(
            "INSERT INTO bronze_tickets(ticket_id, payload, ingested_at) VALUES (?, ?, ?)",
            [
                (str(row.get("ticket_id", "missing-id")), json.dumps(row), timestamp)
                for row in records
            ],
        )


def upsert_silver(records: list[dict]) -> None:
    with connect() as db:
        db.executemany(
            """
            INSERT INTO silver_tickets(
                ticket_id, customer_message, agent_response, category, channel,
                created_at, kb_reference, status, priority, quality_status,
                sentiment, response_minutes
            ) VALUES (
                :ticket_id, :customer_message, :agent_response, :category, :channel,
                :created_at, :kb_reference, :status, :priority, :quality_status,
                :sentiment, :response_minutes
            )
            ON CONFLICT(ticket_id) DO UPDATE SET
                customer_message=excluded.customer_message,
                agent_response=excluded.agent_response,
                category=excluded.category,
                channel=excluded.channel,
                created_at=excluded.created_at,
                kb_reference=excluded.kb_reference,
                status=excluded.status,
                priority=excluded.priority,
                quality_status=excluded.quality_status,
                sentiment=excluded.sentiment,
                response_minutes=excluded.response_minutes
            """,
            [
                {
                    **row,
                    "status": row.get("status", "open"),
                    "priority": row.get("priority", "normal"),
                    "quality_status": row.get("quality_status", "passed"),
                    "sentiment": row.get("sentiment", "neutral"),
                    "response_minutes": float(row.get("response_minutes", 0)),
                }
                for row in records
            ],
        )


def refresh_gold() -> None:
    with connect() as db:
        rows = db.execute(
            """
            SELECT category, substr(created_at, 1, 10) AS day, COUNT(*) AS ticket_count,
                   AVG(length(agent_response)) AS avg_response_length
            FROM silver_tickets
            GROUP BY category, substr(created_at, 1, 10)
            """
        ).fetchall()
        db.executemany(
            """
            INSERT INTO gold_metrics(category, day, ticket_count, avg_response_length)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(category, day) DO UPDATE SET
                ticket_count=excluded.ticket_count,
                avg_response_length=excluded.avg_response_length
            """,
            [
                (row["category"], row["day"], row["ticket_count"], row["avg_response_length"])
                for row in rows
            ],
        )


def get_tickets(
    *,
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses: list[str] = []
    values: list[object] = []
    if status:
        clauses.append("status = ?")
        values.append(status)
    if category:
        clauses.append("category = ?")
        values.append(category)
    if search:
        clauses.append("(customer_message LIKE ? OR ticket_id LIKE ?)")
        values.extend([f"%{search}%", f"%{search}%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as db:
        rows = db.execute(
            f"""
            SELECT ticket_id, customer_message, agent_response, category, channel,
                   created_at, status, priority, quality_status, sentiment, kb_reference
            FROM silver_tickets {where}
            ORDER BY
                CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                created_at DESC
            LIMIT ?
            """,
            [*values, max(1, min(limit, 100))],
        ).fetchall()
    return [dict(row) for row in rows]


def get_ticket(ticket_id: str) -> dict | None:
    with connect() as db:
        row = db.execute(
            """
            SELECT ticket_id, customer_message, agent_response, category, channel,
                   created_at, status, priority, quality_status, sentiment, kb_reference
            FROM silver_tickets WHERE ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()
    return dict(row) if row else None


def silver_records() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT ticket_id, customer_message, agent_response, category, channel,
                   created_at, kb_reference, status, priority, quality_status,
                   sentiment, response_minutes
            FROM silver_tickets
            ORDER BY created_at DESC, ticket_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def all_support_tickets() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT ticket_id, customer_message, agent_response, category, channel,
                   created_at, kb_reference, status, priority, quality_status,
                   sentiment, response_minutes
            FROM silver_tickets
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_knowledge_articles() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT article_id, category, title, content, language, source, updated_at
            FROM knowledge_articles ORDER BY updated_at DESC, article_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def add_knowledge_article(
    *, category: str, title: str, content: str, language: str, source: str | None
) -> dict:
    article = {
        "article_id": f"kb-{uuid.uuid4().hex[:10]}",
        "category": category,
        "title": title.strip(),
        "content": content.strip(),
        "language": language.strip().lower(),
        "source": source.strip() if source and source.strip() else None,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    with connect() as db:
        db.execute(
            """
            INSERT INTO knowledge_articles(
                article_id, category, title, content, language, source, updated_at
            ) VALUES (:article_id, :category, :title, :content, :language, :source, :updated_at)
            """,
            article,
        )
    return article


DEFAULT_FINANCIAL_ASSUMPTIONS = {
    "monthly_tickets": 600,
    "agent_hourly_cost_sar": 75.0,
    "avg_handle_minutes": 12.0,
    "automation_deflection_pct": 15.0,
    "ai_cost_per_ticket_sar": 0.75,
    "implementation_cost_sar": 0.0,
}


def financial_assumptions() -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT monthly_tickets, agent_hourly_cost_sar, avg_handle_minutes,
                   automation_deflection_pct, ai_cost_per_ticket_sar,
                   implementation_cost_sar
            FROM business_settings WHERE id = 1
            """
        ).fetchone()
    return dict(row) if row else dict(DEFAULT_FINANCIAL_ASSUMPTIONS)


def save_financial_assumptions(assumptions: dict) -> dict:
    values = {**DEFAULT_FINANCIAL_ASSUMPTIONS, **assumptions}
    with connect() as db:
        db.execute(
            """
            INSERT INTO business_settings(
                id, monthly_tickets, agent_hourly_cost_sar, avg_handle_minutes,
                automation_deflection_pct, ai_cost_per_ticket_sar,
                implementation_cost_sar, updated_at
            ) VALUES (1, :monthly_tickets, :agent_hourly_cost_sar, :avg_handle_minutes,
                      :automation_deflection_pct, :ai_cost_per_ticket_sar,
                      :implementation_cost_sar, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                monthly_tickets=excluded.monthly_tickets,
                agent_hourly_cost_sar=excluded.agent_hourly_cost_sar,
                avg_handle_minutes=excluded.avg_handle_minutes,
                automation_deflection_pct=excluded.automation_deflection_pct,
                ai_cost_per_ticket_sar=excluded.ai_cost_per_ticket_sar,
                implementation_cost_sar=excluded.implementation_cost_sar,
                updated_at=excluded.updated_at
            """,
            {**values, "updated_at": datetime.now(UTC).isoformat()},
        )
    return values


def apply_escalation(ticket_id: str, reason: str, risk_score: float) -> bool:
    with connect() as db:
        cursor = db.execute(
            "UPDATE silver_tickets SET status = 'escalated' WHERE ticket_id = ? AND status = 'open'",
            (ticket_id,),
        )
        if cursor.rowcount:
            db.execute(
                """
                INSERT INTO automation_events(ticket_id, reason, risk_score, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (ticket_id, reason, risk_score, datetime.now(UTC).isoformat()),
            )
            return True
    return False


def update_ticket_status(ticket_id: str, status: str) -> dict | None:
    with connect() as db:
        cursor = db.execute(
            "UPDATE silver_tickets SET status = ? WHERE ticket_id = ?",
            (status, ticket_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_ticket(ticket_id)


def dashboard_summary() -> dict:
    from datetime import date, timedelta

    today = date.today()
    day_labels = [(today - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
    with connect() as db:
        totals = db.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('open', 'escalated') THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN priority = 'urgent' THEN 1 ELSE 0 END) AS urgent_count,
                   AVG(response_minutes) AS avg_response
            FROM silver_tickets
            """
        ).fetchone()
        raw_count = int(db.execute("SELECT COUNT(*) FROM bronze_tickets").fetchone()[0])
        category_rows = db.execute(
            "SELECT category, COUNT(*) AS count FROM silver_tickets GROUP BY category"
        ).fetchall()
        daily_rows = db.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count
            FROM silver_tickets
            WHERE substr(created_at, 1, 10) >= ?
            GROUP BY substr(created_at, 1, 10)
            """,
            (day_labels[0],),
        ).fetchall()
    by_category = {row["category"]: int(row["count"]) for row in category_rows}
    by_day = {row["day"]: int(row["count"]) for row in daily_rows}
    total = int(totals["total"] or 0)
    return {
        "total_tickets": total,
        "open_tickets": int(totals["open_count"] or 0),
        "urgent_tickets": int(totals["urgent_count"] or 0),
        "quality_pass_rate": round((total / raw_count) * 100 if raw_count else 100.0, 1),
        "avg_response_minutes": round(float(totals["avg_response"] or 0), 1),
        "categories": [
            {"category": category, "count": by_category.get(category, 0)}
            for category in sorted(CATEGORIES)
        ],
        "daily_volume": [{"day": day, "count": by_day.get(day, 0)} for day in day_labels],
    }


def update_stage(name: str, status: str, records: int, detail: str) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO pipeline_stages(name, status, records, last_run_at, detail)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                status=excluded.status, records=excluded.records,
                last_run_at=excluded.last_run_at, detail=excluded.detail
            """,
            (name, status, records, datetime.now(UTC).isoformat(), detail),
        )


def stages() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT name, status, records, last_run_at, detail FROM pipeline_stages ORDER BY rowid"
        ).fetchall()
    return [dict(row) for row in rows]


def save_pipeline_run(run: dict) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO pipeline_runs(
                run_id, started_at, completed_at, received, accepted, rejected, quality_passed
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"], run["started_at"], run["completed_at"],
                run["received"], run["accepted"], run["rejected"],
                int(run["quality_passed"]),
            ),
        )


def last_pipeline_run() -> dict | None:
    with connect() as db:
        row = db.execute(
            "SELECT run_id, started_at, completed_at FROM pipeline_runs ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def record_counts() -> tuple[int, int, int]:
    with connect() as db:
        bronze = int(db.execute("SELECT COUNT(*) FROM bronze_tickets").fetchone()[0])
        silver = int(db.execute("SELECT COUNT(*) FROM silver_tickets").fetchone()[0])
        gold = int(db.execute("SELECT COUNT(*) FROM gold_metrics").fetchone()[0])
    return bronze, silver, gold
