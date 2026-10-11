from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.rag.parsing import Section


@dataclass
class Chunk:
    ordinal: int
    heading: str
    text: str
    page: int | None = None


def _split_units(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for p in paras:
        if len(p) <= 600:
            units.append(p)
        else:
            units.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip())
    return units


def chunk_sections(sections: list[Section], max_chars: int = 900, overlap_chars: int = 150) -> list[Chunk]:
    """Structure-aware chunking: never crosses section boundaries, packs paragraphs/sentences up to
    max_chars and carries a sentence-aligned overlap into the next chunk."""
    chunks: list[Chunk] = []
    for sec in sections:
        buf: list[str] = []
        size = 0
        for unit in _split_units(sec.text):
            if size + len(unit) > max_chars and buf:
                chunks.append(Chunk(len(chunks), sec.heading, "\n".join(buf), sec.page))
                tail: list[str] = []
                t = 0
                for u in reversed(buf):
                    if t + len(u) > overlap_chars:
                        break
                    tail.insert(0, u)
                    t += len(u)
                buf, size = tail, t
            while len(unit) > max_chars:
                chunks.append(Chunk(len(chunks), sec.heading, unit[:max_chars], sec.page))
                unit = unit[max_chars - overlap_chars:]
            buf.append(unit)
            size += len(unit)
        if buf:
            chunks.append(Chunk(len(chunks), sec.heading, "\n".join(buf), sec.page))
    return chunks
