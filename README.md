# Real-Time Customer Support Intelligence Platform

An end-to-end pipeline built in a single Colab notebook, using synthetic support-ticket data.

## Architecture

```
Synthetic Data --> Kafka (KRaft) --> Pydantic Contract --> Delta Lakehouse
                                                              |
                                          Bronze -> Silver -> Gold (MERGE)
                                                              |
                                                     Great Expectations + OpenLineage
                                                              |
                              KB Articles --> Chunking --> Embeddings --> Qdrant + BM25
                                                              |
                                                  Hybrid Search + Cross-Encoder Rerank
                                                              |
                                                     Airflow DAG (orchestrates all of it)
```

## Modules (`pipeline/`)

| Module | Responsibility |
|---|---|
| `data_gen.py` | Synthetic KB articles + support tickets, with injected quality issues |
| `contract.py` | Pydantic data contract (schema validation boundary) |
| `ingest.py` | Kafka producer/consumer |
| `lakehouse.py` | Delta Lake bronze/silver/gold with MERGE + schema enforcement |
| `quality.py` | Great Expectations checkpoint + OpenLineage event emitter |
| `rag.py` | Chunking, embeddings, Qdrant index, hybrid search, cross-encoder reranking |

## Orchestration

`airflow_home/dags/capstone_support_dag.py` defines the DAG
`generate_data -> kafka_ingest -> lakehouse_load -> quality_gate -> build_rag_index`,
run end-to-end via `airflow dags test capstone_support_pipeline <date>`.

## How to run

Open the notebook in Google Colab and use "Run All". Total runtime: roughly 10-15 minutes
(includes downloading Kafka, Spark, and the embedding models).
