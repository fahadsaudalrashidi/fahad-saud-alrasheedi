"""Chunking, dense + BM25 retrieval, reciprocal-rank fusion, and reranking."""

import hashlib
import math
import os
import re
import uuid
from collections import Counter

TOKEN_RE = re.compile(r"[a-z0-9\u0600-\u06FF]+")
COLLECTION = "kb_chunks"


def tokenize(text: str) -> list[str]:
    normalized = []
    for token in TOKEN_RE.findall(text.lower()):
        if len(token) > 5 and token.endswith("ies"):
            token = f"{token[:-3]}y"
        elif len(token) > 5 and token.endswith("ing"):
            token = token[:-3]
        elif len(token) > 4 and token.endswith("ed"):
            token = token[:-2]
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        normalized.append(token)
    return normalized


def chunk_articles(articles: list[dict], max_sentences: int = 2) -> list[dict]:
    chunks = []
    for article in articles:
        sentences = re.split(r"(?<=[.!?؟۔])\s+|\n+", article["content"].strip())
        for start in range(0, len(sentences), max_sentences):
            text = " ".join(sentences[start : start + max_sentences]).strip()
            if text:
                chunks.append(
                    {
                        "chunk_id": str(uuid.uuid4()),
                        "article_id": article["id"],
                        "article_title": article["title"],
                        "category": article["category"],
                        "language": article.get("language", "en"),
                        "text": text,
                    }
                )
    return chunks


def _dense_vector(text: str, dimensions: int = 512) -> list[float]:
    """Stable feature-hash embedding for dependency-light, repeatable local search."""
    counts = Counter(tokenize(text))
    vector = [0.0] * dimensions
    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "big")
        index = number % dimensions
        sign = 1.0 if number & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / length for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class RagIndex:
    def __init__(self, articles: list[dict]):
        self.mode = os.getenv("RAG_MODE", "hash").strip().lower()
        self.chunks = chunk_articles(articles)
        self._vectors: list[list[float]] = []
        self._embed_model = None
        self._reranker = None
        self._qdrant = None
        self._bm25 = None

        if self.mode in ("transformer", "multilingual"):
            self._build_transformer_index()
        elif self.mode == "hash":
            self._vectors = [_dense_vector(chunk["text"]) for chunk in self.chunks]
        else:
            raise ValueError("RAG_MODE must be 'hash', 'transformer', or 'multilingual'.")

        try:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi([tokenize(chunk["text"]) for chunk in self.chunks])
        except ImportError:
            self._bm25 = None

    def _build_transformer_index(self) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, PointStruct, VectorParams
            from sentence_transformers import CrossEncoder, SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "RAG_MODE=transformer requires the optional RAG extra. "
                "Install with `pip install '.[rag]'`."
            ) from exc

        embedding_model = (
            "paraphrase-multilingual-MiniLM-L12-v2"
            if self.mode == "multilingual"
            else "all-MiniLM-L6-v2"
        )
        self._embed_model = SentenceTransformer(embedding_model)
        reranker_model = (
            "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
            if self.mode == "multilingual"
            else "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        self._reranker = CrossEncoder(reranker_model)
        self._qdrant = QdrantClient(":memory:")
        texts = [chunk["text"] for chunk in self.chunks]
        vectors = self._embed_model.encode(texts, normalize_embeddings=True)
        self._vectors = [vector.tolist() for vector in vectors]
        self._qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(
                size=int(vectors.shape[1]), distance=Distance.COSINE
            ),
        )
        self._qdrant.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(id=index, vector=vector, payload=self.chunks[index])
                for index, vector in enumerate(self._vectors)
            ],
        )

    def _bm25_scores(self, query: str) -> list[float]:
        query_tokens = tokenize(query)
        if self._bm25 is not None:
            return [float(score) for score in self._bm25.get_scores(query_tokens)]

        corpus = [tokenize(chunk["text"]) for chunk in self.chunks]
        average_length = sum(map(len, corpus)) / max(len(corpus), 1)
        document_frequency = Counter(
            token for document in corpus for token in set(document)
        )
        total_documents = max(len(corpus), 1)
        scores = []
        for document in corpus:
            frequencies = Counter(document)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                idf = math.log(
                    1 + (total_documents - document_frequency[token] + 0.5)
                    / (document_frequency[token] + 0.5)
                )
                denominator = frequency + 1.5 * (
                    1 - 0.75 + 0.75 * len(document) / max(average_length, 1)
                )
                score += idf * frequency * 2.5 / denominator
            scores.append(score)
        return scores

    def hybrid_search(
        self, query: str, top_k: int = 8, fusion_k: int = 60
    ) -> list[dict]:
        if not self.chunks:
            return []
        if self.mode in ("transformer", "multilingual"):
            vector = self._embed_model.encode(query, normalize_embeddings=True).tolist()
            response = self._qdrant.query_points(
                collection_name=COLLECTION, query=vector, limit=min(20, len(self.chunks))
            )
            dense_order = [int(point.id) for point in response.points]
        else:
            query_vector = _dense_vector(query)
            dense_order = sorted(
                range(len(self.chunks)),
                key=lambda index: _cosine(query_vector, self._vectors[index]),
                reverse=True,
            )[:20]

        sparse_scores = self._bm25_scores(query)
        sparse_order = sorted(
            range(len(sparse_scores)),
            key=lambda index: sparse_scores[index],
            reverse=True,
        )[:20]
        dense_rank = {index: rank for rank, index in enumerate(dense_order)}
        sparse_rank = {index: rank for rank, index in enumerate(sparse_order)}
        candidate_ids = set(dense_rank) | set(sparse_rank)
        fused = []
        for index in candidate_ids:
            score = 0.0
            if index in dense_rank:
                score += 1.0 / (fusion_k + dense_rank[index] + 1)
            if index in sparse_rank:
                score += 1.0 / (fusion_k + sparse_rank[index] + 1)
            fused.append((index, score))
        fused.sort(key=lambda item: item[1], reverse=True)
        return [
            {**self.chunks[index], "fusion_score": score}
            for index, score in fused[:top_k]
        ]

    def rerank(self, query: str, candidates: list[dict], top_n: int = 3) -> list[dict]:
        if not candidates:
            return []
        if self._reranker is not None:
            scores = self._reranker.predict(
                [[query, candidate["text"]] for candidate in candidates]
            )
            ranked = zip(candidates, scores)
        else:
            query_tokens = set(tokenize(query))

            def score(candidate: dict) -> float:
                document_tokens = set(
                    tokenize(f'{candidate["article_title"]} {candidate["text"]}')
                )
                overlap = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
                title_matches = len(
                    query_tokens & set(tokenize(candidate["article_title"]))
                )
                phrase_bonus = (
                    0.2 if " ".join(tokenize(query)) in " ".join(tokenize(candidate["text"])) else 0
                )
                return (
                    overlap
                    + title_matches * 0.12
                    + phrase_bonus
                    + candidate["fusion_score"]
                )

            ranked = ((candidate, score(candidate)) for candidate in candidates)
        ordered = sorted(ranked, key=lambda pair: float(pair[1]), reverse=True)
        return [
            {**candidate, "rerank_score": round(float(score), 4)}
            for candidate, score in ordered[:top_n]
        ]

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        candidates = self.hybrid_search(query, top_k=max(top_k, 8))
        ranked = self.rerank(query, candidates, top_n=len(candidates))
        unique_articles = []
        seen = set()
        for result in ranked:
            if result["article_id"] in seen:
                continue
            seen.add(result["article_id"])
            unique_articles.append(result)
            if len(unique_articles) == top_k:
                break
        return unique_articles
