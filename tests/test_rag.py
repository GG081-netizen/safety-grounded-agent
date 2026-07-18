"""Tests for flattened RAG module."""

import json

from conversation_agent.rag.module import KnowledgeStore, generate_with_citations, rank_and_filter, retrieve


def test_retrieve_loads_candidates(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "doc.json").write_text(json.dumps({
        "source_id": "DOC_1",
        "title": "笔记本采购",
        "text": "批量采购笔记本需要关注保修和交付。",
        "tags": ["笔记本", "采购"],
    }, ensure_ascii=False), encoding="utf-8")

    candidates = retrieve("笔记本采购", store=KnowledgeStore(root))
    assert candidates
    assert candidates[0].source_id == "DOC_1"


def test_rank_and_filter_scores_evidence(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "doc.json").write_text(json.dumps({
        "source_id": "DOC_1",
        "title": "SLA 采购规则",
        "text": "采购合同需要明确 SLA 和交付时间。",
    }, ensure_ascii=False), encoding="utf-8")
    candidates = retrieve("采购 SLA", store=KnowledgeStore(root))
    evidence = rank_and_filter("采购 SLA", candidates)
    assert evidence
    assert evidence[0].confidence > 0


def test_generate_with_citations_returns_sources(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "doc.json").write_text(json.dumps({
        "source_id": "DOC_1",
        "title": "合同规则",
        "text": "标准采购合同需要明确验收标准。",
    }, ensure_ascii=False), encoding="utf-8")
    evidence = rank_and_filter("采购合同", retrieve("采购合同", store=KnowledgeStore(root)))
    result = generate_with_citations("采购合同", evidence)
    assert result.sources
    assert result.confidence > 0


def test_generate_without_evidence_does_not_fake_citation():
    result = generate_with_citations("不存在的问题", [])
    assert result.sources == []
    assert result.confidence < 0.5
    assert result.warnings
