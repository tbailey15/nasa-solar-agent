"""Split the NASA documents into chunks and find the chunks that match a query.

Scoring is BM25, written out by hand so every step is easy to inspect.
Each chunk keeps its page title, source URL and section heading.
"""

import math
import re
from collections import Counter
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
CHUNK_CHARS = 700     # target chunk size
OVERLAP_CHARS = 120   # text repeated between neighbouring chunks so facts are not cut in half

STOPWORDS = set("""a an and are as at be by for from has have how in is it its of on or
that the this to was were what which with about does did do than into many much""".split())

PLANETS = ["mercury", "venus", "earth", "mars", "jupiter", "saturn", "uranus", "neptune"]


def tokenize(text):
    """Lowercase words and numbers, without stopwords."""
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


def read_doc(path):
    """Return (title, url, body) from one Markdown file made by prepare_docs.py."""
    text = path.read_text(encoding="utf-8")
    title = text.splitlines()[0].lstrip("# ").strip()
    url = re.search(r"^Source: (\S+)", text, re.M).group(1)
    body = text.split("\n---\n", 1)[1]
    return title, url, body


def split_sections(body):
    """Split a document on its ## / ### headings into (heading, text) pairs."""
    sections, heading, lines = [], "Overview", []
    for line in body.splitlines():
        if line.startswith("#"):
            if lines:
                sections.append((heading, "\n".join(lines).strip()))
            heading, lines = line.lstrip("# ").strip(), []
        elif line.strip():
            lines.append(line)
    if lines:
        sections.append((heading, "\n".join(lines).strip()))
    return sections


def make_chunks():
    """Break every document into overlapping chunks with metadata."""
    chunks = []
    for path in sorted(DOCS.glob("*.md")):
        title, url, body = read_doc(path)
        for heading, text in split_sections(body):
            start = 0
            while start < len(text):
                piece = text[start:start + CHUNK_CHARS]
                chunks.append({
                    "id": f"{path.stem}-{len(chunks)}",
                    "doc": path.stem,
                    "title": title,
                    "url": url,
                    "section": heading,
                    "text": piece,
                })
                if start + CHUNK_CHARS >= len(text):
                    break
                start += CHUNK_CHARS - OVERLAP_CHARS
    return chunks


class Retriever:
    """BM25 search over the chunks."""

    def __init__(self, k1=1.5, b=0.75):
        self.chunks = make_chunks()
        self.k1, self.b = k1, b
        self.tokens = [tokenize(c["section"] + " " + c["text"]) for c in self.chunks]
        self.avg_len = sum(len(t) for t in self.tokens) / len(self.tokens)
        doc_freq = Counter(word for toks in self.tokens for word in set(toks))
        n = len(self.chunks)
        self.idf = {w: math.log(1 + (n - df + 0.5) / (df + 0.5)) for w, df in doc_freq.items()}

    def score(self, query_tokens, i):
        tf = Counter(self.tokens[i])
        length = len(self.tokens[i])
        total = 0.0
        for word in query_tokens:
            if word in tf:
                freq = tf[word]
                total += self.idf[word] * freq * (self.k1 + 1) / (
                    freq + self.k1 * (1 - self.b + self.b * length / self.avg_len))
        return total

    def search(self, query, k=3, planet=None):
        """Return the k best chunks. If planet is given, only that planet's page is searched."""
        query_tokens = tokenize(query)
        candidates = range(len(self.chunks))
        if planet:
            candidates = [i for i in candidates if self.chunks[i]["doc"] == planet.lower()]
        ranked = sorted(candidates, key=lambda i: self.score(query_tokens, i), reverse=True)
        return [dict(self.chunks[i], score=round(self.score(query_tokens, i), 2))
                for i in ranked[:k] if self.score(query_tokens, i) > 0]

    def get(self, chunk_id):
        return next((c for c in self.chunks if c["id"] == chunk_id), None)
