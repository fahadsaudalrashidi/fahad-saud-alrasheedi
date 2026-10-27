# Fahad Technology Solutions — Support Intelligence

A bilingual real-time customer-support operations console based on the public notebook at
`fahadsaudalrashidi/fahad-saud-alrasheedi`. The workspace contains a React
executive dashboard, an Express API gateway, and a modular FastAPI processing
service.

The product brand is **Fahad Technology Solutions / فهد للحلول التقنية**.

## What is implemented

- **Ticket operations:** contract-validated ticket ingestion, search and filters,
  priority/sentiment signals, status updates, and ticket analytics.
- **Pipeline:** local transport or an explicitly configured Kafka
  publish/consume round-trip; append-only Bronze storage; validated, deduplicated
  Silver upserts; Gold category/day aggregates; a callable pipeline run endpoint.
  The notebook's stage order is triggered through the API; this app does not
  run an Airflow scheduler or claim a Kafka cluster is active by default.
- **Quality and lineage:** built-in Silver contract checks, optional Great
  Expectations checkpoint, and OpenLineage-compatible run events written to
  JSONL or emitted with the optional OpenLineage client.
- **Knowledge assistant:** sentence chunking, dense + BM25 retrieval, reciprocal
  rank fusion (`k=60`), and reranking. The default feature-hash retriever runs
  without model downloads; optional transformer mode uses
  `all-MiniLM-L6-v2`, in-memory Qdrant, and
  `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- **Grounded AI chat:** in Replit, the Express gateway calls the managed OpenAI
  integration and includes retrieved article citations. The standalone
  FastAPI/Docker app uses a retrieval-grounded suggested reply when no model
  integration is configured.
- **Bilingual knowledge:** persisted English and Arabic knowledge articles are
  indexed for hybrid retrieval; multilingual transformer embeddings are
  available as an optional mode.
- **Operations and commercial strategy:** heuristic SLA/sentiment signals,
  audited escalation preview/apply, editable SAR cost assumptions, and a
  cited market/competitor strategy view in Arabic and English.

The repository contains synthetic sample tickets and knowledge-base policies,
not a live customer-data source. Default local mode is self-contained and
reports its infrastructure as local rather than claiming Kafka, Spark, or a
remote vector database is connected.
SLA scores use explicit demo thresholds and are not a trained or validated
forecast. ROI figures are scenarios built from editable assumptions, not
measured company costs or financial advice. Market pricing and buyer profiles
are research hypotheses; they are not Saudi-validated offers.

## Run in Replit

The configured `artifacts/api-server: API Server` workflow starts the Express
gateway and its Python sidecar. The `artifacts/support-intelligence: web`
workflow serves the dashboard. Both should be running to use the full console.
The managed OpenAI integration is configured for assistant-generated replies;
its credentials are supplied by Replit and are not stored in this repository.

The API service uses `PYTHON_PORT=8031` by default and starts Python from
`.pythonlibs/bin/python`. Set `PYTHON_EXECUTABLE` if the runtime uses another
interpreter. The API gateway is the only public entry point for the Python
service.

## Standalone container

Build and run:

```sh
docker build -t support-intelligence .
docker run --rm -p 8000:8000 -v support-data:/data support-intelligence
```

The mounted `/data` volume retains SQLite tickets, knowledge articles,
assumptions, audit events, and lineage events between container restarts. The
API and built dashboard are served from the same container. `/api/docs` exposes
the FastAPI OpenAPI page and `/api/healthz` is the health check. Use
`.env.example` as a reference for runtime settings; do not commit a populated
`.env` file. The container defaults to `/data`; if you pass a local `.env` file,
set `SUPPORT_DB_PATH` and `LINEAGE_LOG_PATH` to `/data/...` so the named volume
retains state.

## Optional source infrastructure

The lightweight defaults are intended for a single app instance. Optional
adapters are declared in `pyproject.toml`:

```sh
pip install '.[streaming]'          # Kafka producer/consumer
pip install '.[lakehouse]'          # Spark + Delta Lake adapter
pip install '.[quality]'            # Great Expectations + OpenLineage client
pip install '.[rag]'                # SentenceTransformers + Qdrant + BM25
pip install '.[full-pipeline]'      # all optional pipeline dependencies
```

Configure `KAFKA_BOOTSTRAP_SERVERS`, `LAKEHOUSE_MODE=delta`,
`QUALITY_MODE=great-expectations`, `OPENLINEAGE_MODE=client`, or
`RAG_MODE=transformer` / `RAG_MODE=multilingual` only after installing the corresponding extra and
providing the infrastructure those modes require. Missing optional packages
or unreachable brokers fail with an explicit error instead of silently
pretending the integration is active.

For multi-instance production hosting, use shared durable storage and a shared
database/lakehouse rather than the default per-instance SQLite file. The
included SQLite mode is a practical, durable single-instance deployment path;
the Delta adapter writes the notebook-style Bronze/Silver/Gold layers when
Spark and a supported storage path are configured.

## Tests

Run the backend smoke tests with:

```sh
python -m unittest discover -s tests
```
