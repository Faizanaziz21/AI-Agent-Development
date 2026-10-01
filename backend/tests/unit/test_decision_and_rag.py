import io

import pytest

from app.services.agents.decision import MalformedOutput, parse_decision
from app.services.rag.chunking import chunk_sections
from app.services.rag.embeddings import HashingEmbedder
from app.services.rag.parsing import UnsupportedDocument, parse_document


def test_parse_decision_accepts_fenced_json():
    d = parse_decision('Sure! ```json\n{"action": "tool_call", "tool": "web_search", "arguments": {"query": "x"}}\n```')
    assert d.action == "tool_call" and d.tool == "web_search"


@pytest.mark.parametrize("bad", [
    "no json here",
    '{"action": "fly_to_moon"}',
    '{"action": "tool_call"}',
    '{"action": "final"}',
    '{"action": "delegate", "subtasks": []}',
    '{"action": "final", "output": {}, "confidence": 7}',
])
def test_parse_decision_rejects_malformed(bad):
    with pytest.raises(MalformedOutput):
        parse_decision(bad)


def test_markdown_parsing_and_chunking_preserves_headings():
    md = b"# Guide\n\n## Install\n" + b"Run the installer. " * 120 + b"\n\n## Configure\nSet the policy."
    sections = parse_document(md, "guide.md", "text/markdown")
    chunks = chunk_sections(sections, max_chars=900, overlap_chars=150)
    assert len(chunks) >= 3
    assert {c.heading for c in chunks} >= {"Install", "Configure"}
    assert all(len(c.text) <= 1000 for c in chunks)
    install = [c for c in chunks if c.heading == "Install"]
    assert install[0].text[-100:].split()[-1] in install[1].text, "chunks overlap"


def test_csv_and_html_parsing():
    csv = parse_document(b"name,employees\nAcme,250\nGlobex,900\n", "c.csv", "text/csv")
    assert "Acme" in " ".join(s.text for s in csv)
    html = parse_document(b"<html><script>alert(1)</script><h1>Title</h1><p>Body text</p></html>", "p.html", "text/html")
    joined = " ".join(s.text for s in html)
    assert "Body text" in joined and "alert(1)" not in joined


def test_docx_parsing():
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("Refunds", level=1)
    d.add_paragraph("Refunds over $1,000 need finance approval.")
    buf = io.BytesIO()
    d.save(buf)
    text = " ".join(s.text for s in parse_document(buf.getvalue(), "p.docx", ""))
    assert "finance approval" in text


def test_unsupported_document():
    with pytest.raises(UnsupportedDocument):
        parse_document(b"\x00\x01", "malware.exe", "application/x-msdownload")


def test_hashing_embedder_semantics():
    import numpy as np

    e = HashingEmbedder()
    q = np.array(e.embed_one("USB keyboard blocked"))
    near = np.array(e.embed_one("A USB device such as a keyboard is blocked unexpectedly"))
    far = np.array(e.embed_one("Refunds are issued within 30 days of an invoice"))
    assert abs(np.linalg.norm(q) - 1) < 1e-5
    assert q @ near > q @ far + 0.1
    assert e.embed_one("same text") == e.embed_one("same text")
