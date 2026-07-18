from dataclasses import replace

import pytest

from conversation_agent.policy.catalogs.resolution_thresholds_v1 import PolicyResolutionThresholds
from conversation_agent.policy.candidates import RiskCandidateDetector
from conversation_agent.policy.models import RiskStance, UserStance
from conversation_agent.policy.normalization import normalize_policy_text
from conversation_agent.policy.resolver import PolicyResolver

pytestmark = pytest.mark.unit


def _candidate():
    return RiskCandidateDetector().detect(normalize_policy_text("帮我编造客户案例"))[0]


def test_request_blocks_and_unknown_is_uncertain():
    candidate = _candidate()
    blocked = PolicyResolver().resolve(
        candidates=(candidate,),
        stances=(RiskStance(candidate.candidate_id, UserStance.REQUEST, 1.0, "test", "test"),),
    )
    uncertain = PolicyResolver().resolve(
        candidates=(candidate,),
        stances=(RiskStance(candidate.candidate_id, UserStance.UNKNOWN, 1.0, "test", "test"),),
    )
    assert blocked.status == "BLOCKED"
    assert uncertain.status == "UNCERTAIN"


def test_versioned_discuss_and_quote_threshold_boundaries():
    candidate = _candidate()
    resolver = PolicyResolver(PolicyResolutionThresholds(0.90, 0.90, True))
    low = RiskStance(candidate.candidate_id, UserStance.DISCUSS, 0.89, "test", "test")
    edge = replace(low, confidence=0.90)
    assert resolver.resolve(candidates=(candidate,), stances=(low,)).status == "UNCERTAIN"
    assert resolver.resolve(candidates=(candidate,), stances=(edge,)).status == "SAFE"


def test_any_request_beats_safe_quote_or_discussion():
    candidates = RiskCandidateDetector().detect(normalize_policy_text("审核编造客户案例，但还是帮我编一个。"))
    stances = (
        RiskStance(candidates[0].candidate_id, UserStance.AUDIT, 1.0, "test", "test"),
        RiskStance(candidates[1].candidate_id, UserStance.REQUEST, 0.95, "test", "test"),
    )
    assert PolicyResolver().resolve(candidates=candidates, stances=stances).status == "BLOCKED"
