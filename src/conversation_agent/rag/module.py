"""Small local-file RAG module with citations.

V1 intentionally stays flat: retrieve(), rank_and_filter(), and
 generate_with_citations().  No vector DB, no framework dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conversation_agent.config import get_config
from conversation_agent.rag.models import Evidence, RagResult

_TOKEN_RE = re.compile(r"[\w一-鿿]+", re.UNICODE)


class KnowledgeStore:
    """Load local JSON/Markdown/Text knowledge records."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or (get_config().storage.data_dir / "knowledge"))

    def load(self) -> list[Evidence]:
        if not self.root.exists():
            return []
        records: list[Evidence] = []
        for path in sorted(self.root.rglob("*")):
            if path.is_dir() or path.suffix.lower() not in {".json", ".md", ".txt"}:
                continue
            record = self._load_file(path)
            if record is not None:
                records.append(record)
        return records

    def _load_file(self, path: Path) -> Evidence | None:
        try:
            raw = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".json":
                data = json.loads(raw)
                return Evidence(
                    source_id=str(data.get("source_id") or path.stem),
                    title=str(data.get("title") or path.stem),
                    text=str(data.get("text") or data.get("content") or ""),
                    source_path=str(path),
                    category=str(data.get("category") or path.parent.name),
                    tags=list(data.get("tags") or []),
                )
            title = path.stem.replace("_", " ")
            first_line = raw.strip().splitlines()[0] if raw.strip() else title
            if first_line.startswith("#"):
                title = first_line.lstrip("# ").strip() or title
            return Evidence(
                source_id=path.stem,
                title=title,
                text=raw.strip(),
                source_path=str(path),
                category=path.parent.name,
            )
        except (OSError, json.JSONDecodeError, ValueError):
            return None


def retrieve(query: str, store: KnowledgeStore | None = None, limit: int = 20) -> list[Evidence]:
    """Load candidate evidence from the local knowledge store."""
    records = (store or KnowledgeStore()).load()
    if not query.strip():
        return records[:limit]
    q_tokens = _tokens(query)
    if not q_tokens:
        return records[:limit]
    candidates: list[Evidence] = []
    for record in records:
        haystack = " ".join([record.title, record.text, record.category, " ".join(record.tags)])
        overlap = len(q_tokens & _tokens(haystack))
        if overlap > 0:
            item = record.model_copy()
            item.score = float(overlap)
            candidates.append(item)
    return sorted(candidates, key=lambda e: e.score, reverse=True)[:limit]


def rank_and_filter(query: str, candidates: list[Evidence], top_k: int = 3, min_score: float = 1.0) -> list[Evidence]:
    """Rank evidence and compute per-evidence confidence."""
    q_tokens = _tokens(query)
    ranked: list[Evidence] = []
    for candidate in candidates:
        text_tokens = _tokens(" ".join([candidate.title, candidate.text, " ".join(candidate.tags)]))
        overlap = len(q_tokens & text_tokens)
        density = overlap / max(len(q_tokens), 1)
        score = candidate.score or float(overlap)
        if score < min_score:
            continue
        item = candidate.model_copy()
        item.score = score
        item.confidence = max(0.1, min(1.0, density))
        ranked.append(item)
    ranked.sort(key=lambda e: (e.confidence, e.score), reverse=True)
    return ranked[:top_k]


def generate_with_citations(query: str, evidence: list[Evidence]) -> RagResult:
    """Generate a grounded answer using only the supplied evidence."""
    if not evidence:
        return RagResult(
            answer="未在本地知识库中找到足够证据，无法给出可引用结论。",
            evidence=[],
            sources=[],
            confidence=0.15,
            warnings=["no_evidence"],
        )

    bullets = []
    for idx, item in enumerate(evidence, start=1):
        snippet = _snippet(item.text)
        bullets.append(f"[{idx}] {item.title}: {snippet}")
    avg_conf = sum(item.confidence for item in evidence) / len(evidence)
    sources = [
        {
            "source_id": item.source_id,
            "title": item.title,
            "source_path": item.source_path,
            "category": item.category,
            "confidence": round(item.confidence, 2),
        }
        for item in evidence
    ]
    return RagResult(
        answer="基于已检索证据，建议参考以下结论：\n" + "\n".join(bullets),
        evidence=evidence,
        sources=sources,
        confidence=round(min(0.95, max(0.2, avg_conf)), 2),
    )


def _tokens(text: str) -> set[str]:
    raw = text or ""
    tokens = {tok.lower() for tok in _TOKEN_RE.findall(raw) if tok.strip()}
    cjk = [ch for ch in raw if "一" <= ch <= "鿿"]
    tokens.update(cjk)
    tokens.update("".join(cjk[i : i + 2]) for i in range(max(0, len(cjk) - 1)))
    return tokens


def _snippet(text: str, max_len: int = 120) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1] + "..."
