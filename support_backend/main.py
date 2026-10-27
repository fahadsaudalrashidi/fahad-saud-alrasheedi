"""FastAPI application serving the support API and, in containers, the built web app."""

import os
import re
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from support_backend.contracts import CATEGORIES
from support_backend.data_gen import (
    AGENT_RESPONSES,
    KB_ARTICLES,
    generate_tickets,
    infer_priority,
)
from support_backend.pipeline import run_pipeline
from support_backend.operations import (
    financial_scenario,
    operations_analytics,
    run_escalation_automation,
    update_financial_scenario,
)
from support_backend.rag import RagIndex
from support_backend.store import (
    add_knowledge_article,
    dashboard_summary,
    list_knowledge_articles,
    get_ticket,
    get_tickets,
    initialize_store,
    last_pipeline_run,
    record_counts,
    stages,
    update_ticket_status,
)

rag_index: RagIndex | None = None


class TicketInput(BaseModel):
    customer_message: str = Field(min_length=5, max_length=4000)
    category: str
    agent_response: str | None = Field(default=None, max_length=4000)
    channel: str = Field(default="web", max_length=40)


class TicketUpdate(BaseModel):
    status: Literal["open", "escalated", "resolved"]


class PipelineInput(BaseModel):
    batch_size: int = Field(default=100, ge=1, le=1000)


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=10)


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    ticket_id: str | None = None


class KnowledgeArticleInput(BaseModel):
    category: str
    title: str = Field(min_length=3, max_length=200)
    content: str = Field(min_length=20, max_length=12000)
    language: str = Field(min_length=2, max_length=12)
    source: str | None = Field(default=None, max_length=500)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", value):
            raise ValueError("language must be a language tag such as en or ar-SA")
        return value.lower()

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError("Unknown support category.")
        return value

class FinancialAssumptionsInput(BaseModel):
    monthly_tickets: int = Field(ge=1, le=10_000_000)
    agent_hourly_cost_sar: float = Field(ge=1, le=1_000_000)
    avg_handle_minutes: float = Field(ge=1, le=1440)
    automation_deflection_pct: float = Field(ge=0, le=100)
    ai_cost_per_ticket_sar: float = Field(ge=0, le=1_000_000)
    implementation_cost_sar: float = Field(ge=0, le=1_000_000_000)


class AutomationInput(BaseModel):
    dry_run: bool = True
    limit: int = Field(default=1000, ge=1, le=1000)


def _ensure_rag_index() -> RagIndex:
    global rag_index
    if rag_index is None:
        rows = list_knowledge_articles()
        rag_index = RagIndex(
            [
                {
                    "id": row["article_id"],
                    "category": row["category"],
                    "title": row["title"],
                    "content": row["content"],
                    "language": row["language"],
                }
                for row in rows
            ]
        )
    return rag_index


def _rebuild_rag_index() -> int:
    global rag_index
    rag_index = None
    index = _ensure_rag_index()
    return len(index.chunks)


def _broker_reachable() -> bool:
    servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not servers:
        return False
    for server in servers.split(","):
        host, separator, raw_port = server.strip().rpartition(":")
        if not separator:
            continue
        try:
            with socket.create_connection((host.strip("[]"), int(raw_port)), timeout=0.5):
                return True
        except (OSError, ValueError):
            continue
    return False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_store()
    _ensure_rag_index()
    _bronze, silver, _gold = record_counts()
    if silver == 0:
        run_pipeline(generate_tickets(600), rebuild_rag=_rebuild_rag_index)
    yield


app = FastAPI(
    title="Support Intelligence API",
    version="1.0.0",
    description="Ticket processing, pipeline observability, hybrid knowledge search, and grounded agent assist.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/api/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/support/dashboard")
def support_dashboard() -> dict:
    return dashboard_summary()


@app.get("/api/support/tickets")
def support_tickets(
    status: Literal["open", "escalated", "resolved"] | None = None,
    category: str | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict]:
    if category and category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown ticket category.")
    return get_tickets(status=status, category=category, search=search, limit=limit)


@app.post("/api/support/tickets", status_code=201)
def create_support_ticket(body: TicketInput) -> dict:
    if body.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown ticket category.")
    message = body.customer_message.strip()
    if len(message) < 5:
        raise HTTPException(
            status_code=400,
            detail="The ticket contract requires a customer message of at least 5 characters.",
        )

    article = None
    if not body.agent_response:
        results = _ensure_rag_index().search(message, top_k=1)
        article = results[0] if results else None
    kb_reference = article["article_id"] if article else None
    response = (
        body.agent_response.strip()
        if body.agent_response and body.agent_response.strip()
        else (AGENT_RESPONSES.get(kb_reference, "") if kb_reference else "")
    )
    if not response:
        raise HTTPException(
            status_code=400,
            detail="No relevant knowledge article was found. Add an agent response before ingesting this ticket.",
        )

    priority, sentiment = infer_priority(message)
    record = {
        "ticket_id": f"T-{__import__('uuid').uuid4().hex[:10]}",
        "customer_message": message,
        "agent_response": response,
        "category": body.category,
        "channel": body.channel,
        "created_at": datetime.now(UTC).isoformat(),
        "kb_reference": kb_reference,
        "status": "open",
        "priority": priority,
        "quality_status": "passed",
        "sentiment": sentiment,
        "response_minutes": 0,
    }
    result = run_pipeline([record], rebuild_rag=_rebuild_rag_index)
    if result["accepted"] != 1:
        raise HTTPException(status_code=400, detail="Ticket failed the support data contract.")
    created = get_ticket(record["ticket_id"])
    if created is None:
        raise HTTPException(status_code=500, detail="Ticket was accepted but could not be loaded.")
    return created


@app.patch("/api/support/tickets/{ticket_id}")
def patch_support_ticket(ticket_id: str, body: TicketUpdate) -> dict:
    updated = update_ticket_status(ticket_id, body.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return updated


@app.get("/api/support/pipeline/health")
def pipeline_health() -> dict:
    current_stages = stages()
    last_run = last_pipeline_run()
    statuses = [stage["status"] for stage in current_stages]
    overall = (
        "degraded"
        if "warning" in statuses
        else "healthy"
        if any(status == "healthy" for status in statuses)
        else "idle"
    )
    kafka_configured = bool(os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip())
    kafka_reachable = _broker_reachable() if kafka_configured else False
    mode = "kafka" if kafka_reachable else "local"

    api_stages = [
        {
            "name": stage["name"],
            "status": stage["status"],
            "records": stage["records"],
            "last_run_at": stage["last_run_at"],
            "detail": stage["detail"],
        }
        for stage in current_stages
    ]
    if "Quality gate" in {stage["name"] for stage in api_stages}:
        pass
    else:
        api_stages.append(
            {
                "name": "Quality gate",
                "status": "idle",
                "records": 0,
                "last_run_at": None,
                "detail": "Waiting for the first quality check",
            }
        )

    return {
        "mode": mode,
        "overall_status": overall,
        "stages": api_stages,
        "infrastructure": [
            {
                "name": "Kafka broker",
                "status": "connected" if kafka_reachable else (
                    "unavailable" if kafka_configured else "local"
                ),
                "detail": (
                    "Broker connection verified"
                    if kafka_reachable
                    else "Configured broker could not be reached"
                    if kafka_configured
                    else "Using the in-process transport; set KAFKA_BOOTSTRAP_SERVERS for Kafka"
                ),
            },
            {
                "name": "Ticket lakehouse",
                "status": "connected" if os.getenv("LAKEHOUSE_MODE", "sqlite").lower() == "delta" else "local",
                "detail": (
                    f"Delta Lakehouse is writing to {os.getenv('DELTA_LAKEHOUSE_PATH', 'data/delta_lakehouse')}"
                    if os.getenv("LAKEHOUSE_MODE", "sqlite").lower() == "delta"
                    else "SQLite-backed Bronze/Silver/Gold tables are active"
                ),
            },
            {
                "name": "Hybrid RAG index",
                "status": "connected" if _ensure_rag_index().mode in ("transformer", "multilingual") else "local",
                "detail": (
                    "Multilingual sentence embeddings, Qdrant, BM25, and cross-encoder"
                    if _ensure_rag_index().mode == "multilingual"
                    else "SentenceTransformer, Qdrant, BM25, and cross-encoder"
                    if _ensure_rag_index().mode == "transformer"
                    else "Feature-hash dense retrieval, BM25, RRF, and local reranking"
                ),
            },
            {
                "name": "OpenLineage events",
                "status": "local",
                "detail": "Run lifecycle events are appended to the configured JSONL file",
            },
        ],
        "last_run_at": last_run["completed_at"] if last_run else None,
    }


@app.get("/api/support/market-insights")
def get_market_insights(language: str = Query(default="en", pattern="^(en|ar)$")) -> dict:
    from support_backend.market_strategy import get_market_strategy

    return get_market_strategy(language)


@app.get("/api/support/operations")
def get_operations() -> dict:
    return operations_analytics()


@app.get("/api/support/operations/financials")
def get_financials() -> dict:
    return financial_scenario()


@app.patch("/api/support/operations/financials")
def patch_financials(body: FinancialAssumptionsInput) -> dict:
    return update_financial_scenario(body.model_dump())


@app.post("/api/support/operations/automation/run")
def run_automation(body: AutomationInput) -> dict:
    return run_escalation_automation(dry_run=body.dry_run, limit=body.limit)


@app.post("/api/support/pipeline/run")
def run_support_pipeline(body: PipelineInput) -> dict:
    return run_pipeline(generate_tickets(body.batch_size), rebuild_rag=_rebuild_rag_index)


@app.get("/api/support/knowledge")
def knowledge_articles() -> list[dict]:
    return list_knowledge_articles()


@app.post("/api/support/knowledge", status_code=201)
def create_knowledge_article(body: KnowledgeArticleInput) -> dict:
    article = add_knowledge_article(
        category=body.category,
        title=body.title,
        content=body.content,
        language=body.language,
        source=body.source,
    )
    _rebuild_rag_index()
    return article


@app.post("/api/support/search")
def search_knowledge(body: SearchInput) -> dict:
    results = _ensure_rag_index().search(body.query, top_k=body.limit)
    return {"query": body.query, "results": results}


@app.post("/api/support/chat")
def ask_support_assistant(body: ChatInput) -> dict:
    results = _ensure_rag_index().search(body.message, top_k=3)
    citations = [
        {
            "article_id": item["article_id"],
            "title": item["article_title"],
            "category": item["category"],
            "excerpt": item["text"],
        }
        for item in results
    ]
    if results:
        best = results[0]
        answer = (
            f"Suggested response: {AGENT_RESPONSES.get(best['article_id'], best['text'])}"
        )
        confidence = max(0.0, min(1.0, float(best["rerank_score"])))
    else:
        answer = "I could not find a sufficiently relevant article. Please review the knowledge base or escalate this ticket."
        confidence = 0.0
    return {
        "answer": answer,
        "citations": citations,
        "model": "retrieval-only",
        "confidence": confidence,
    }


static_dir = os.getenv("WEB_STATIC_DIR")
if static_dir and Path(static_dir).is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "support_backend.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("APP_ENV") == "development",
    )
