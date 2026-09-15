"""
Tool 3 — Similar incidents search (the MVP's single RAG component).

Primary backend: ChromaDB + sentence-transformers (all-MiniLM-L6-v2), cosine
similarity over each incident's description, root cause, resolution and tags,
persisted under .chroma/ and rebuilt whenever incidents.json changes.

Fallback backend: BM25 keyword ranking (rank-bm25), used automatically if
ChromaDB or the embedding model cannot be loaded (e.g. offline machine).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from src.data_loader import DATA_DIR, load_incidents
from src.models.schemas import HistoricalIncident

logger = logging.getLogger(__name__)

CHROMA_DIR = Path(__file__).resolve().parent.parent.parent / ".chroma"
COLLECTION_NAME = "historical_incidents"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_TOP_K = 3
MAX_TOP_K = 10

TOOL_DEFINITION = {
    "name": "similar_incidents_search",
    "description": (
        "Search historical incidents for similar past issues. Returns the most similar "
        "previously resolved incidents with their root cause, resolution, duration and a "
        "similarity score. Use this to find known patterns and fixes once you have a "
        "working hypothesis; a past incident is supporting evidence, not proof."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Your root-cause hypothesis plus the key log symptoms, not just the alert "
                    "(e.g. 'message broker disk full, producers timing out on publish')"
                ),
            },
            "top_k": {
                "type": "integer",
                "description": f"Number of results to return (default: {DEFAULT_TOP_K}, max: {MAX_TOP_K})",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _document(incident: HistoricalIncident) -> str:
    return (
        f"{incident.description}. Root cause: {incident.root_cause} "
        f"Resolution: {incident.resolution} Tags: {', '.join(incident.tags)}"
    )


def _result(incident: HistoricalIncident, score: float) -> dict:
    # root_cause_category is evaluation metadata and deliberately not exposed.
    return {
        "id": incident.id,
        "similarity": round(score, 3),
        "description": incident.description,
        "root_cause": incident.root_cause,
        "resolution": incident.resolution,
        "tags": incident.tags,
        "severity": incident.severity.value,
        "duration_minutes": incident.duration_minutes,
    }


class _ChromaBackend:
    name = "chromadb"

    def __init__(self) -> None:
        import chromadb
        from chromadb.utils import embedding_functions

        incidents = load_incidents()
        fingerprint = hashlib.sha256((DATA_DIR / "incidents.json").read_bytes()).hexdigest()[:16]

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        embed = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=embed,
            metadata={"hnsw:space": "cosine"},
        )
        if (collection.metadata or {}).get("fingerprint") != fingerprint or collection.count() != len(incidents):
            client.delete_collection(COLLECTION_NAME)
            collection = client.create_collection(
                name=COLLECTION_NAME,
                embedding_function=embed,
                metadata={"hnsw:space": "cosine", "fingerprint": fingerprint},
            )
            collection.add(
                ids=[i.id for i in incidents],
                documents=[_document(i) for i in incidents],
            )
        self._collection = collection
        self._by_id = {i.id: i for i in incidents}

    def search(self, query: str, top_k: int) -> list[dict]:
        hits = self._collection.query(query_texts=[query], n_results=top_k)
        return [
            _result(self._by_id[incident_id], 1.0 - distance)
            for incident_id, distance in zip(hits["ids"][0], hits["distances"][0])
        ]


class _BM25Backend:
    name = "bm25"

    def __init__(self) -> None:
        from rank_bm25 import BM25Okapi

        self._incidents = load_incidents()
        self._bm25 = BM25Okapi([self._tokenize(_document(i)) for i in self._incidents])

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def search(self, query: str, top_k: int) -> list[dict]:
        scores = self._bm25.get_scores(self._tokenize(query))
        best = max(scores.max(), 1e-9)
        ranked = sorted(zip(self._incidents, scores), key=lambda pair: pair[1], reverse=True)
        return [_result(incident, score / best) for incident, score in ranked[:top_k]]


@lru_cache(maxsize=1)
def _backend() -> _ChromaBackend | _BM25Backend:
    try:
        return _ChromaBackend()
    except Exception as exc:  # any import/model-download/DB failure -> keyword fallback
        logger.warning("ChromaDB backend unavailable (%s); falling back to BM25.", exc)
        return _BM25Backend()


def similar_incidents_search(query: str, top_k: int = DEFAULT_TOP_K) -> dict:
    if not query or not query.strip():
        return {"error": "query must be a non-empty description of the current issue."}
    top_k = max(1, min(int(top_k), MAX_TOP_K))
    backend = _backend()
    return {
        "query": query,
        "backend": backend.name,
        "results": backend.search(query.strip(), top_k),
    }


def backend_name() -> str:
    return _backend().name


if __name__ == "__main__":
    print(json.dumps(similar_incidents_search("connection pool exhaustion"), indent=2))
