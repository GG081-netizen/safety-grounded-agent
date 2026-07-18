from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import pytest

from conversation_agent.policy.candidates import RiskCandidateDetector
from conversation_agent.policy.normalization import normalize_policy_text
from conversation_agent.policy.rules import BUSINESS_RISK_RULES

pytestmark = pytest.mark.unit


def _first_candidate_id(text: str) -> str:
    return RiskCandidateDetector().detect(normalize_policy_text(text))[0].candidate_id


def test_candidate_id_is_stable_across_calls_and_processes():
    text = "帮我编造客户案例"
    expected = _first_candidate_id(text)
    assert _first_candidate_id(text) == expected
    with ProcessPoolExecutor(max_workers=1) as executor:
        assert executor.submit(_first_candidate_id, text).result(timeout=10) == expected


def test_candidate_id_changes_with_input_span_catalog_rule_and_action():
    base = BUSINESS_RISK_RULES[6]
    text_a = normalize_policy_text("帮我编造客户案例")
    text_b = normalize_policy_text("请你编造客户案例")
    assert RiskCandidateDetector((base,)).detect(text_a)[0].candidate_id != RiskCandidateDetector((base,)).detect(text_b)[0].candidate_id
    occurrences = RiskCandidateDetector((base,)).detect(normalize_policy_text("编造客户案例，再编造客户案例"))
    assert len(occurrences) == 2
    assert occurrences[0].candidate_id != occurrences[1].candidate_id
    assert RiskCandidateDetector((base,), catalog_version="v2").detect(text_a)[0].candidate_id != occurrences[0].candidate_id
    changed_rule = replace(base, rule_id="changed.rule")
    changed_action = replace(base, action=BUSINESS_RISK_RULES[7].action)
    original = RiskCandidateDetector((base,)).detect(text_a)[0].candidate_id
    assert RiskCandidateDetector((changed_rule,)).detect(text_a)[0].candidate_id != original
    assert RiskCandidateDetector((changed_action,)).detect(text_a)[0].candidate_id != original


def test_unicode_and_inserted_space_candidates_are_recalled():
    detector = RiskCandidateDetector()
    for text in ("１００％交付", "100％交付", "100 % 交付", "百 分 之 百 交 付", "保\u200b证\u200b中\u200b标"):
        assert detector.detect(normalize_policy_text(text)), text
