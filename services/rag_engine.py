"""
RAG engine — embedding + FAISS retrieval.

1. On startup / refresh:
   - Load text chunks from Google Sheets
   - Combine with static company knowledge
   - Embed all chunks via OpenAI text-embedding-3-small
   - Store in a FAISS index

2. At query time:
   - Embed the user query
   - Retrieve top-K most relevant chunks
   - Return as context string for the LLM
"""

import os
import time
import numpy as np
import faiss
from openai import OpenAI

from config import settings
from logger_config import logger

_index_initialized = False
_index_cache = None

# ---- Module-level state ----
_index: faiss.IndexFlatIP | None = None  # cosine similarity via inner product on normalized vectors
_chunks: list[str] = []
_initialized: bool = False

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536  # dimension of text-embedding-3-small


def _embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a batch of texts using OpenAI API. Returns (N, 1536) float32 array."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # OpenAI allows up to 2048 inputs per call; chunk if needed
    all_embeddings = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        for item in response.data:
            all_embeddings.append(item.embedding)

    arr = np.array(all_embeddings, dtype="float32")
    # L2-normalize for cosine similarity via inner product
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1
    arr = arr / norms
    return arr


def build_index(chunks: list[str]) -> None:
    """Build (or rebuild) the FAISS index from text chunks."""
    global _index, _chunks, _initialized

    if not chunks:
        logger.warning("rag_engine.build_index: no chunks to index")
        _initialized = True
        return

    logger.info("rag_engine: building FAISS index for %d chunks…", len(chunks))
    start = time.time()

    embeddings = _embed_texts(chunks)
    index = faiss.IndexFlatIP(EMBEDDING_DIM)  # inner-product (cosine on normalized vecs)
    index.add(embeddings)

    _index = index
    _chunks = list(chunks)
    _initialized = True

    elapsed = time.time() - start
    logger.info("rag_engine: FAISS index built in %.2fs  (%d vectors)", elapsed, index.ntotal)


def initialize() -> None:
    """
    One-time initialization: load chunks from Google Sheets + static KB,
    embed them, and build the FAISS index.
    """
    global _index_initialized, _index_cache
    
    if _index_initialized and _index_cache:
        logger.debug(
            "rag_engine: index already "
            "cached, skipping rebuild"
        )
        return _index_cache

    global _initialized
    if _initialized:
        return

    all_chunks: list[str] = []

    # 1. Static company knowledge
    try:
        from knowledge_base import COMPANY_KNOWLEDGE
        for key, value in COMPANY_KNOWLEDGE.items():
            val = str(value).strip()
            if val:
                all_chunks.append(f"[{key}] {val}")
    except Exception as e:
        logger.warning("rag_engine: failed to load static KB: %s", e)

    # 2. Google Sheets data
    # Google Sheets disabled until
    # sheet name is configured
    sheets_chunks = []

    # 3. Load chunks from knowledge_base DB table
    try:
        from db_config import (
            get_connection, release_connection
        )
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT category, chunk_text 
                FROM knowledge_base 
                WHERE chunk_text IS NOT NULL
                  AND chunk_text != ''
                ORDER BY created_at ASC
                """
            )
            rows = cur.fetchall()
            cur.close()
            db_chunks = [
                f"[{r[0]}] {r[1]}"
                for r in rows
                if r[1] and r[1].strip()
            ]
            all_chunks.extend(db_chunks)
            logger.info(
                "rag_engine: %d chunks from DB,"
                " %d total",
                len(db_chunks), len(all_chunks)
            )
        finally:
            release_connection(conn)
    except Exception as e:
        logger.warning(
            "rag_engine: DB chunks load "
            "skipped: %s", e
        )

    if all_chunks:
        build_index(all_chunks)
    else:
        logger.warning("rag_engine: no chunks available, RAG disabled")
        _initialized = True
        
    global _index
    _index_initialized = True
    _index_cache = _index
    logger.info(
        "rag_engine: index cached, "
        "will not rebuild until refresh"
    )


def retrieve(query: str, top_k: int = 5) -> str:
    """
    Embed the query and retrieve the top-K most relevant chunks.

    """
    _SKIP_RAG = {
        "hi", "hello", "hey", "hiya",
        "bye", "goodbye", "thanks",
        "thank you", "ok", "okay",
        "yes", "no", "sure", "great",
        "sounds good", "perfect"
    }
    
    query_lower = query.lower().strip(
        "?!., "
    )
    
    if query_lower in _SKIP_RAG:
        logger.debug(
            "rag_engine: skipping RAG "
            "for simple query='%s'",
            query
        )
        return ""

    global _index, _chunks, _initialized

    if not _initialized:
        try:
            initialize()
        except Exception as e:
            logger.error("rag_engine: lazy init failed: %s", e)
            return ""

    if _index is None or not _chunks:
        return ""

    try:
        query_vec = _embed_texts([query])
        k = min(top_k, len(_chunks))
        distances, indices = _index.search(query_vec, k)

        results = []
        for idx, dist in zip(indices[0], distances[0]):
            if idx < 0 or idx >= len(_chunks):
                continue
            results.append(_chunks[idx])

        if results:
            logger.info("rag_engine: retrieved %d chunks (scores: %s)",
                        len(results), [f"{d:.3f}" for d in distances[0][:len(results)]])

        return "\n---\n".join(results)

    except Exception as e:
        logger.error("rag_engine: retrieval failed: %s", e)
        return ""


def refresh_index():
    global _index_initialized, \
           _index_cache
    _index_initialized = False
    _index_cache = None
    logger.info(
        "rag_engine: cache cleared, "
        "will rebuild on next request"
    )
    return initialize()
