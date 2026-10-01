"""Knowledge / RAG service: ingestion pipeline and hybrid retrieval with citations."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telemetry import INJECTION_FLAGS
from app.models import KnowledgeChunk, KnowledgeSource
from app.services.rag.chunking import chunk_sections
from app.services.rag.embeddings import _stem, get_embedder, tokenize
from app.services.rag.parsing import parse_document
from app.services.security.injection import neutralize, scan

RRF_K = 60


@dataclass
class Passage:
    chunk_id: str
    source_id: str
    source_name: str
    heading: str
    text: str
    score: float
    classification: str
    source_type: str
    semantic_rank: int | None = None
    keyword_rank: int | None = None
    injection_risk: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def citation(self, idx: int) -> dict:
        return {"id": f"S{idx}", "chunk_id": self.chunk_id, "source_id": self.source_id,
                "source": self.source_name, "section": self.heading, "score": round(self.score, 4)}


@dataclass
class _IndexEntry:
    chunk_id: str
    source_id: str
    tokens: Counter
    length: int


class _OrgIndex:
    """Per-organization in-memory index (vectors + BM25 postings), rebuilt when the corpus changes."""

    def __init__(self, version: tuple, rows: list[tuple[KnowledgeChunk, KnowledgeSource]]):
        self.version = version
        self.rows = rows
        self.matrix = np.array([r[0].embedding or [] for r in rows], dtype=np.float32) if rows else np.zeros((0, 1))
        self.entries = []
        df: Counter = Counter()
        for ch, _src in rows:
            toks = Counter(_stem(t) for t in tokenize(f"{ch.heading} {ch.text}"))
            self.entries.append(_IndexEntry(ch.id, ch.source_id, toks, sum(toks.values())))
            df.update(toks.keys())
        n = max(len(rows), 1)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.avgdl = (sum(e.length for e in self.entries) / n) if self.entries else 1.0

    def bm25(self, query: str, k1: float = 1.4, b: float = 0.75) -> np.ndarray:
        q = [_stem(t) for t in tokenize(query)]
        scores = np.zeros(len(self.entries), dtype=np.float32)
        for i, e in enumerate(self.entries):
            s = 0.0
            for t in q:
                tf = e.tokens.get(t, 0)
                if tf:
                    s += self.idf.get(t, 0) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * e.length / self.avgdl))
            scores[i] = s
        return scores


class VectorIndexBackend(ABC):
    @abstractmethod
    async def index_for(self, session: AsyncSession, org_id: str) -> _OrgIndex: ...


class SqlVectorBackend(VectorIndexBackend):
    """Embeddings stored with chunks; similarity computed in-process with numpy.
    Production: PgVectorBackend (HNSW index, `ORDER BY embedding <=> :q`) — see docs/RAG_ARCHITECTURE.md."""

    def __init__(self) -> None:
        self._cache: dict[str, _OrgIndex] = {}

    async def index_for(self, session: AsyncSession, org_id: str) -> _OrgIndex:
        version = tuple((await session.execute(
            select(func.count(KnowledgeChunk.id), func.max(KnowledgeChunk.created_at)).where(KnowledgeChunk.org_id == org_id)
        )).one())
        cached = self._cache.get(org_id)
        if cached and cached.version == version:
            return cached
        rows = (await session.execute(
            select(KnowledgeChunk, KnowledgeSource).join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
            .where(KnowledgeChunk.org_id == org_id, KnowledgeSource.status == "INDEXED")
            .order_by(KnowledgeChunk.source_id, KnowledgeChunk.ordinal)
        )).all()
        idx = _OrgIndex(version, [(r[0], r[1]) for r in rows])
        self._cache[org_id] = idx
        return idx


backend: VectorIndexBackend = SqlVectorBackend()


async def ingest(session: AsyncSession, source: KnowledgeSource, data: bytes) -> KnowledgeSource:
    """parse → sanitise → chunk → injection scan → embed → index."""
    try:
        sections = parse_document(data, source.filename or source.name, source.mime_type)
        chunks = chunk_sections(sections)
        if not chunks:
            raise ValueError("document contains no extractable text")
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
        embedder = get_embedder()
        vectors = await embedder.embed([f"{c.heading}\n{c.text}" for c in chunks])
        flagged = 0
        for c, vec in zip(chunks, vectors, strict=True):
            res = scan(c.text)
            if res.flagged:
                flagged += 1
                INJECTION_FLAGS.labels("knowledge").inc()
            session.add(KnowledgeChunk(
                org_id=source.org_id, source_id=source.id, ordinal=c.ordinal, heading=c.heading[:300], text=c.text,
                embedding=vec, token_count=len(c.text) // 4, injection_risk=res.risk,
                meta={"page": c.page, "indicators": res.indicators, "embedder": embedder.name},
            ))
        source.chunk_count = len(chunks)
        source.injection_flags = flagged
        source.status = "INDEXED"
        source.error = None
    except Exception as exc:  # noqa: BLE001
        source.status = "FAILED"
        source.error = str(exc)[:500]
    await session.flush()
    return source


async def search(
    session: AsyncSession,
    org_id: str,
    query: str,
    *,
    k: int = 5,
    mode: str = "hybrid",
    source_types: list[str] | None = None,
    tags: list[str] | None = None,
    allowed_classifications: set[str] | None = None,
    source_ids: list[str] | None = None,
) -> list[Passage]:
    idx = await backend.index_for(session, org_id)
    if not idx.rows:
        return []
    mask = np.ones(len(idx.rows), dtype=bool)
    for i, (_ch, src) in enumerate(idx.rows):
        if allowed_classifications is not None and src.classification not in allowed_classifications:
            mask[i] = False
        elif source_types and src.source_type not in source_types:
            mask[i] = False
        elif tags and not set(tags) & set(src.tags or []):
            mask[i] = False
        elif source_ids and src.id not in source_ids:
            mask[i] = False
    if not mask.any():
        return []

    sem_rank: dict[int, int] = {}
    kw_rank: dict[int, int] = {}
    sem_scores = np.zeros(len(idx.rows))
    if mode in ("hybrid", "semantic"):
        qv = np.array((await get_embedder().embed([query]))[0], dtype=np.float32)
        sem_scores = idx.matrix @ qv
        sem_scores[~mask] = -np.inf
        for r, i in enumerate(np.argsort(-sem_scores)[: k * 4]):
            if np.isfinite(sem_scores[i]) and sem_scores[i] > 0.02:
                sem_rank[int(i)] = r + 1
    if mode in ("hybrid", "keyword"):
        kw = idx.bm25(query)
        kw[~mask] = 0
        for r, i in enumerate(np.argsort(-kw)[: k * 4]):
            if kw[i] > 0:
                kw_rank[int(i)] = r + 1

    fused: dict[int, float] = {}
    for i, r in sem_rank.items():
        fused[i] = fused.get(i, 0) + 1 / (RRF_K + r)
    for i, r in kw_rank.items():
        fused[i] = fused.get(i, 0) + 1 / (RRF_K + r)
    out: list[Passage] = []
    for i in sorted(fused, key=lambda j: -fused[j])[:k]:
        ch, src = idx.rows[i]
        text = neutralize(ch.text) if ch.injection_risk >= 0.5 else ch.text
        out.append(Passage(
            chunk_id=ch.id, source_id=src.id, source_name=src.name, heading=ch.heading, text=text,
            score=fused[i], classification=src.classification, source_type=src.source_type,
            semantic_rank=sem_rank.get(i), keyword_rank=kw_rank.get(i), injection_risk=ch.injection_risk,
            meta={"page": (ch.meta or {}).get("page")},
        ))
    return out
