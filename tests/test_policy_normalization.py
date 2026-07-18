import pytest

from conversation_agent.policy.normalization import normalize_policy_text

pytestmark = pytest.mark.unit


def test_nfkc_zero_width_and_spacing_normalization_preserves_offsets():
    result = normalize_policy_text("保\u200b证 １００％ 交付")
    assert result.normalized == "保证 100% 交付"
    assert result.compact == "保证100%交付"
    assert result.normalized_to_raw[result.compact_to_normalized[0]] == 0
    assert result.normalized_to_raw[result.compact_to_normalized[-1]] == len(result.raw) - 1


def test_control_characters_become_collapsed_spaces():
    result = normalize_policy_text("编造\x00\x01客户案例")
    assert result.normalized == "编造 客户案例"
    assert result.compact == "编造客户案例"
