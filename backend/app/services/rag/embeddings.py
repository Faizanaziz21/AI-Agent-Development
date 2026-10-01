from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

import httpx
import numpy as np

from app.core.config import get_settings

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "we you your our can not do does how what when which who why".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP]


def _stem(t: str) -> str:
    for suf in ("ing", "ies", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


class Embedder(ABC):
    dim: int
    name: str

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder(Embedder):
    """Local embedding model: signed feature hashing of stemmed unigrams, bigrams and character
    trigrams with sublinear TF, L2-normalised. Deterministic, dependency-free, and good enough for
    lexical-semantic retrieval in development; swap for OpenAIEmbedder in production."""

    name = "local-hash-384"

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _features(self, text: str) -> dict[str, float]:
        toks = [_stem(t) for t in tokenize(text)]
        feats: dict[str, float] = {}
        for t in toks:
            feats["u:" + t] = feats.get("u:" + t, 0) + 1.0
            if len(t) > 4:
                for i in range(len(t) - 2):
                    g = "c:" + t[i: i + 3]
                    feats[g] = feats.get(g, 0) + 0.25
        for a, b in zip(toks, toks[1:], strict=False):
            feats[f"b:{a}_{b}"] = feats.get(f"b:{a}_{b}", 0) + 0.7
        return feats

    def embed_one(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for f, w in self._features(text).items():
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            weight = 1 + math.log(w) if w >= 1 else w
            vec[h % self.dim] += weight if (h >> 63) & 1 else -weight
        n = np.linalg.norm(vec)
        return (vec / n).tolist() if n > 0 else vec.tolist()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class OpenAIEmbedder(Embedder):
    name = "text-embedding-3-small"
    dim = 1536

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://api.openai.com/v1/embeddings",
                                  headers={"Authorization": f"Bearer {self.api_key}"},
                                  json={"model": self.name, "input": texts})
            r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        s = get_settings()
        _embedder = OpenAIEmbedder(s.openai_api_key) if s.embedding_provider == "openai" and s.openai_api_key else HashingEmbedder()
    return _embedder
