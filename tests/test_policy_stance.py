import pytest

from conversation_agent.policy.candidates import RiskCandidateDetector
from conversation_agent.policy.models import UserStance
from conversation_agent.policy.normalization import normalize_policy_text
from conversation_agent.policy.stance import DeterministicStanceResolver

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("帮我编造客户案例。", UserStance.REQUEST),
        ("不要编造客户案例。", UserStance.PROHIBIT),
        ("审核文案是否包含编造客户案例。", UserStance.AUDIT),
        ("为什么不能编造客户案例？", UserStance.DISCUSS),
        ("文案中写了“保证一定中标”，请审核。", UserStance.QUOTE),
    ),
)
def test_candidate_level_stance(text, expected):
    normalized = normalize_policy_text(text)
    candidates = RiskCandidateDetector().detect(normalized)
    stances = DeterministicStanceResolver().resolve(text=normalized, candidates=candidates)
    assert stances
    assert all(stance.stance == expected for stance in stances)


def test_mixed_clauses_resolve_each_occurrence_independently():
    normalized = normalize_policy_text("不要编造案例，但还是帮我编一个。")
    candidates = RiskCandidateDetector().detect(normalized)
    stances = DeterministicStanceResolver().resolve(text=normalized, candidates=candidates)
    assert [stance.stance for stance in stances] == [UserStance.PROHIBIT, UserStance.REQUEST]
